// cloudrig-admin is the only privileged component. It has no dependency on the Python CLI.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode"

	"golang.org/x/sys/unix"
	"golang.org/x/term"
)

const stateRoot = "/var/db/cloudrig"
const runRoot = "/var/run/cloudrig"
const apiSocket = runRoot + "/api.sock"

func helperPath() string {
	if runtime.GOOS == "darwin" {
		return "/Library/PrivilegedHelperTools/cloudrig-admin"
	}
	return "/usr/local/libexec/cloudrig-admin"
}

var idPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$`)
var namePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$`)
var errDenied = errors.New("operation requires fresh sudo authorization")

type Config struct {
	UID int `json:"uid"`
	GID int `json:"gid"`
}
type Record struct {
	Provider   string `json:"provider"`
	ID         string `json:"id"`
	Name       string `json:"name"`
	Host       string `json:"host"`
	Port       int    `json:"port"`
	User       string `json:"user"`
	Key        string `json:"key"`
	KnownHosts string `json:"known_hosts"`
	HostKey    string `json:"host_key"`
	Revoked    bool   `json:"revoked"`
}
type DB struct {
	Records map[string]Record `json:"records"`
	Pending map[string]bool   `json:"pending"`
}
type Request struct {
	Op       string `json:"op"`
	Provider string `json:"provider"`
	ID       string `json:"id,omitempty"`
	Query    string `json:"query,omitempty"`
	Count    int    `json:"count,omitempty"`
	Disk     int    `json:"disk,omitempty"`
}
type Offer struct {
	ID           string   `json:"id"`
	Provider     string   `json:"provider"`
	GPU          string   `json:"gpu"`
	Count        int      `json:"count"`
	VRAM         float64  `json:"vram"`
	Hourly       float64  `json:"hourly"`
	Region       string   `json:"region"`
	Availability string   `json:"availability"`
	Cloud        string   `json:"cloud"`
	DiskRate     *float64 `json:"disk_hourly_gb"`
	DiskMax      *float64 `json:"disk_max"`
	Reliability  *float64 `json:"reliability"`
}
type Instance struct {
	ID           string  `json:"id"`
	Provider     string  `json:"provider"`
	Name         string  `json:"name"`
	GPU          string  `json:"gpu"`
	Count        int     `json:"count"`
	Status       string  `json:"status"`
	Hourly       float64 `json:"hourly"`
	Region       string  `json:"region"`
	Host         string  `json:"host"`
	Port         int     `json:"port"`
	User         string  `json:"user"`
	Direct       bool    `json:"direct"`
	PublicKey    string  `json:"public_key,omitempty"`
	AgentSocket  string  `json:"agent_socket,omitempty"`
	KnownHosts   string  `json:"known_hosts,omitempty"`
	Approved     bool    `json:"approved"`
	ContractRate float64 `json:"-"`
}
type Store struct {
	Root, Run string
	Owner     Config
	Client    *http.Client
	Base      map[string]string
	Confirm   func(string) error
}

func newStore(root, run string, cfg Config) *Store {
	return &Store{root, run, cfg, &http.Client{Transport: &http.Transport{Proxy: nil}, Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, map[string]string{"vastai": "https://console.vast.ai", "runpod": "https://api.runpod.io"}, ttyConfirm}
}
func (s *Store) readDB() (DB, error) {
	db := DB{map[string]Record{}, map[string]bool{}}
	data, e := os.ReadFile(filepath.Join(s.Root, "resources.json"))
	if os.IsNotExist(e) {
		return db, nil
	}
	if e != nil {
		return db, e
	}
	e = json.Unmarshal(data, &db)
	if db.Records == nil {
		db.Records = map[string]Record{}
	}
	if db.Pending == nil {
		db.Pending = map[string]bool{}
	}
	return db, e
}
func writeJSON(path string, value any) error {
	data, e := json.MarshalIndent(value, "", "  ")
	if e != nil {
		return e
	}
	return atomic(path, append(data, '\n'), 0600)
}
func atomic(path string, data []byte, mode os.FileMode) error {
	f, e := os.CreateTemp(filepath.Dir(path), ".crig-")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	if e = f.Chmod(mode); e == nil {
		_, e = f.Write(data)
	}
	if e == nil {
		e = f.Sync()
	}
	closeErr := f.Close()
	if e == nil {
		e = closeErr
	}
	if e != nil {
		return e
	}
	return os.Rename(f.Name(), path)
}
func (s *Store) lock() (func(), error) {
	f, e := os.OpenFile(filepath.Join(s.Root, ".lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	if e = unix.Flock(int(f.Fd()), unix.LOCK_EX); e != nil {
		f.Close()
		return nil, e
	}
	return func() { unix.Flock(int(f.Fd()), unix.LOCK_UN); f.Close() }, nil
}
func keyOf(provider, id string) string { return provider + "-" + id }
func publicDir(root, provider, id string) string {
	digest := sha256.Sum256([]byte(provider + "/" + id))
	return filepath.Join(root, fmt.Sprintf("%x", digest[:16]))
}

func validProvider(p string) bool { return p == "vastai" || p == "runpod" }
func validNumber(n float64) bool  { return !math.IsNaN(n) && !math.IsInf(n, 0) && n >= 0 }
func text(v any) string {
	if v == nil {
		return ""
	}
	switch x := v.(type) {
	case string:
		return x
	case float64:
		return strconv.FormatFloat(x, 'f', -1, 64)
	}
	return ""
}
func num(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case string:
		if n, e := strconv.ParseFloat(x, 64); e == nil {
			return n
		}
		return math.NaN()
	}
	if v != nil {
		return math.NaN()
	}
	return 0
}
func object(v any) map[string]any {
	m, _ := v.(map[string]any)
	if m == nil {
		return map[string]any{}
	}
	return m
}
func array(v any) []any { x, _ := v.([]any); return x }
func fallback(a, b string) string {
	if a != "" {
		return a
	}
	return b
}
func safeLabel(v string) bool {
	if len(v) > 1024 {
		return false
	}
	for _, c := range v {
		if unicode.IsControl(c) || unicode.Is(unicode.Cf, c) {
			return false
		}
	}
	return true
}
func (s *Store) call(p, method, path string, body any) (any, error) {
	if !validProvider(p) || !strings.HasPrefix(path, "/api/") && p == "vastai" || !strings.HasPrefix(path, "/v2/") && p == "runpod" {
		return nil, errors.New("invalid provider path")
	}
	token, e := os.ReadFile(filepath.Join(s.Root, p+".token"))
	if e != nil {
		return nil, errors.New("provider is not linked; run crig login with sudo authorization")
	}
	var reader io.Reader
	if body != nil {
		data, e := json.Marshal(body)
		if e != nil {
			return nil, e
		}
		reader = bytes.NewReader(data)
	}
	req, e := http.NewRequest(method, s.Base[p]+path, reader)
	if e != nil {
		return nil, e
	}
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(token)))
	req.Header.Set("Content-Type", "application/json")
	resp, e := s.Client.Do(req)
	if e != nil {
		return nil, errors.New("provider connection failed; check rented instances before repeating a mutation")
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("provider returned HTTP %d; response details withheld", resp.StatusCode)
	}
	if resp.StatusCode == 204 {
		return map[string]any{}, nil
	}
	var result any
	if e = json.NewDecoder(io.LimitReader(resp.Body, 32<<20)).Decode(&result); e != nil {
		return nil, errors.New("invalid provider response")
	}
	if object(result)["success"] == false {
		return nil, errors.New("provider declined operation; check account before retrying")
	}
	return result, nil
}
func (s *Store) offers(r Request) ([]Offer, error) {
	if !validProvider(r.Provider) || len(r.Query) > 128 || !safeLabel(r.Query) || r.Disk < 1 || r.Disk > 100000 || r.Count < 1 || r.Count > 64 {
		return nil, errors.New("invalid search")
	}
	out := []Offer{}
	if r.Provider == "vastai" {
		v, e := s.call("vastai", "GET", "/api/v0/gpu_names/unique/", nil)
		if e != nil {
			return nil, e
		}
		names := []string{}
		for _, v := range array(object(v)["gpu_names"]) {
			name := text(v)
			if strings.Contains(strings.ToLower(name), strings.ToLower(r.Query)) {
				names = append(names, name)
			}
		}
		if len(names) == 0 {
			return out, nil
		}
		q := map[string]any{"gpu_name": map[string]any{"in": names}, "verified": map[string]any{"eq": true}, "rentable": map[string]any{"eq": true}, "rented": map[string]any{"eq": false}, "external": map[string]any{"eq": false}, "num_gpus": map[string]any{"gte": r.Count}, "disk_space": map[string]any{"gte": r.Disk}, "direct_port_count": map[string]any{"gte": 1}, "type": "ondemand", "order": [][]string{{"dph_total", "asc"}}, "limit": 10000, "allocated_storage": r.Disk}
		v, e = s.call("vastai", "POST", "/api/v0/bundles/", q)
		if e != nil {
			return nil, e
		}
		for _, v := range array(object(v)["offers"]) {
			row := object(v)
			o := Offer{ID: text(row["id"]), Provider: "vastai", GPU: strings.ReplaceAll(text(row["gpu_name"]), "_", " "), Count: int(num(row["num_gpus"])), VRAM: num(row["gpu_ram"]) / 1024, Hourly: num(row["dph_base"]), Region: fallback(text(row["geolocation"]), "Unknown"), Availability: "available"}
			if !idPattern.MatchString(o.ID) || row["dph_base"] == nil && row["dph_total"] == nil {
				return nil, errors.New("invalid offer or missing price")
			}
			if row["storage_cost"] != nil {
				n := num(row["storage_cost"]) / 720
				o.DiskRate = &n
			}
			if row["dph_base"] == nil {
				o.Hourly = num(row["dph_total"])
				if o.DiskRate != nil {
					o.Hourly -= float64(r.Disk) * (*o.DiskRate)
				}
			}
			if row["disk_space"] != nil {
				n := num(row["disk_space"])
				o.DiskMax = &n
			}
			if row["reliability2"] != nil {
				n := num(row["reliability2"])
				o.Reliability = &n
			}
			out = append(out, o)
		}
	} else {
		for _, cloud := range []string{"SECURE", "COMMUNITY"} {
			v, e := s.call("runpod", "GET", fmt.Sprintf("/v2/catalog/gpus?include=AVAILABILITY&product=POD&count=%d&cloud=%s", r.Count, cloud), nil)
			if e != nil {
				return nil, e
			}
			for _, v := range array(object(v)["gpus"]) {
				row := object(v)
				if !strings.Contains(strings.ToLower(text(row["name"])), strings.ToLower(r.Query)) || row[strings.ToLower(cloud)] != true || text(row["availability"]) == "NONE" || num(object(row["maxCount"])[strings.ToLower(cloud)]) < float64(r.Count) {
					continue
				}
				price := object(row["price"])[strings.ToLower(cloud)]
				if price == nil {
					continue
				}
				for _, v := range array(row["dataCenters"]) {
					c := object(v)
					if text(c["availability"]) == "NONE" {
						continue
					}
					out = append(out, Offer{ID: text(row["id"]) + "|" + cloud + "|" + text(c["id"]), Provider: "runpod", GPU: text(row["name"]), Count: r.Count, VRAM: num(row["memory"]), Hourly: num(price) * float64(r.Count), Region: text(c["id"]), Cloud: cloud, Availability: strings.ToLower(text(c["availability"]))})
				}
			}
		}
	}
	for _, o := range out {
		if !validNumber(o.Hourly) || o.VRAM <= 0 || !validNumber(o.VRAM) || o.Count < 1 || !safeLabel(o.GPU) || !safeLabel(o.Region) || o.DiskRate != nil && !validNumber(*o.DiskRate) || o.DiskMax != nil && (!validNumber(*o.DiskMax) || *o.DiskMax < float64(r.Disk)) {
			return nil, errors.New("invalid provider quote")
		}
	}
	return out, nil
}
func (s *Store) instances(provider string) ([]Instance, error) {
	result := []Instance{}
	cursor := ""
	seen := map[string]bool{}
	for {
		var path string
		if provider == "vastai" {
			path = "/api/v1/instances/?limit=100&after_token=" + url.QueryEscape(cursor)
		} else {
			path = "/v2/pods?limit=100&cursor=" + url.QueryEscape(cursor)
		}
		v, e := s.call(provider, "GET", path, nil)
		if e != nil {
			return nil, e
		}
		data := object(v)
		field := "instances"
		if provider == "runpod" {
			field = "pods"
		}
		for _, v := range array(data[field]) {
			i, e := parseInstance(provider, object(v))
			if e != nil {
				return nil, e
			}
			result = append(result, i)
		}
		if provider == "vastai" {
			cursor = text(data["next_token"])
		} else {
			cursor = text(object(data["pagination"])["nextCursor"])
		}
		if cursor == "" {
			return result, nil
		}
		if seen[cursor] {
			return nil, errors.New("provider repeated pagination cursor")
		}
		seen[cursor] = true
	}
}
func parseInstance(provider string, row map[string]any) (Instance, error) {
	i := Instance{Provider: provider, ID: text(row["id"]), User: "root", Direct: true}
	if !idPattern.MatchString(i.ID) {
		return i, errors.New("invalid instance identifier")
	}
	if provider == "vastai" {
		i.Name = fallback(text(row["label"]), i.ID)
		i.GPU = strings.ReplaceAll(text(row["gpu_name"]), "_", " ")
		i.Count = int(num(row["num_gpus"]))
		i.Status = strings.ToLower(text(row["actual_status"]))
		i.Hourly = num(row["dph_total"])
		i.ContractRate = num(row["dph_base"])
		i.Region = text(row["geolocation"])
		ports := array(object(row["ports"])["22/tcp"])
		if len(ports) > 0 {
			i.Host = text(row["public_ipaddr"])
			i.Port = int(num(object(ports[0])["HostPort"]))
		} else {
			i.Host = text(row["ssh_host"])
			i.Port = int(num(row["ssh_port"]))
		}
	} else {
		i.Name = fallback(text(row["name"]), i.ID)
		i.GPU = text(object(row["gpu"])["id"])
		i.Count = int(num(object(row["gpu"])["count"]))
		i.Status = strings.ToLower(text(row["status"]))
		i.Hourly = num(row["cost"])
		i.Region = text(row["dataCenterId"])
		ssh := object(row["ssh"])
		endpoint := object(ssh["direct"])
		if len(endpoint) == 0 {
			i.Direct = false
			endpoint = object(ssh["proxy"])
		}
		i.Host = text(endpoint["host"])
		i.Port = int(num(endpoint["port"]))
		i.User = fallback(text(endpoint["username"]), "root")
	}
	if i.Count < 1 {
		i.Count = 1
	}
	if !safeLabel(i.Name) || !safeLabel(i.GPU) || !validNumber(i.Hourly) || !validNumber(i.ContractRate) {
		return i, errors.New("invalid instance metadata")
	}
	return i, nil
}
func (s *Store) instance(provider, id string) (Instance, error) {
	if !idPattern.MatchString(id) {
		return Instance{}, errors.New("invalid ID")
	}
	if provider == "runpod" {
		v, e := s.call(provider, "GET", "/v2/pods/"+id, nil)
		if e != nil {
			return Instance{}, e
		}
		return parseInstance(provider, object(v))
	}
	rows, e := s.instances(provider)
	if e != nil {
		return Instance{}, e
	}
	for _, i := range rows {
		if i.ID == id {
			return i, nil
		}
	}
	return Instance{}, errors.New("instance not found")
}
func (s *Store) approved(p, id string) (Record, error) {
	db, e := s.readDB()
	if e != nil {
		return Record{}, e
	}
	rec, ok := db.Records[keyOf(p, id)]
	if !ok || rec.Revoked || rec.Key == "" {
		return rec, errors.New("instance is not approved; run crig NAME approve with sudo authorization")
	}
	return rec, nil
}
func (s *Store) publicGrant(i Instance) Instance {
	if rec, e := s.approved(i.Provider, i.ID); e == nil && validEndpoint(rec) {
		dir := publicDir(s.Run, i.Provider, i.ID)
		i.Approved = true
		i.PublicKey = filepath.Join(dir, "identity.pub")
		i.AgentSocket = filepath.Join(dir, "agent.sock")
		i.KnownHosts = filepath.Join(dir, "known_hosts")
		i.Host = rec.Host
		i.Port = rec.Port
		i.User = rec.User
	}
	return i
}
func (s *Store) public(r Request) (any, error) {
	if !validProvider(r.Provider) {
		return nil, errors.New("unknown provider")
	}
	switch r.Op {
	case "offers":
		return s.offers(r)
	case "instances":
		rows, e := s.instances(r.Provider)
		if e != nil {
			return nil, e
		}
		for n, i := range rows {
			rows[n] = s.publicGrant(i)
		}
		return rows, nil
	case "instance":
		i, e := s.instance(r.Provider, r.ID)
		if e != nil {
			return nil, e
		}
		return s.publicGrant(i), nil
	case "stop":
		if _, e := s.approved(r.Provider, r.ID); e != nil {
			return nil, e
		}
		return s.transition(r.Provider, r.ID, "stop")
	default:
		return nil, errDenied
	}
}
func (s *Store) transition(p, id, action string) (any, error) {
	if !idPattern.MatchString(id) {
		return nil, errors.New("invalid ID")
	}
	if p == "vastai" {
		method := "PUT"
		path := "/api/v0/instances/" + id + "/"
		body := any(map[string]string{"state": map[string]string{"start": "running", "stop": "stopped"}[action]})
		if action == "destroy" {
			method = "DELETE"
			body = nil
		}
		return s.call(p, method, path, body)
	}
	if action == "destroy" {
		return s.call(p, "DELETE", "/v2/pods/"+id, nil)
	}
	return s.call(p, "POST", "/v2/pods/"+id+"/action", map[string]string{"action": action})
}
func socketListener(path string, uid, gid int) (*net.UnixListener, error) {
	os.Remove(path)
	addr := &net.UnixAddr{Name: path, Net: "unix"}
	l, e := net.ListenUnix("unix", addr)
	if e != nil {
		return nil, e
	}
	if e = os.Chmod(path, 0600); e == nil {
		e = os.Chown(path, uid, gid)
	}
	if e != nil {
		l.Close()
		return nil, e
	}
	return l, nil
}
func (s *Store) serveAPI(l *net.UnixListener) {
	for {
		c, e := l.AcceptUnix()
		if e != nil {
			return
		}
		go func() {
			defer c.Close()
			if uid, e := peerUID(c); e != nil || uid != uint32(s.Owner.UID) && uid != 0 {
				return
			}
			c.SetDeadline(time.Now().Add(60 * time.Second))
			decoder := json.NewDecoder(io.LimitReader(c, 16384))
			decoder.DisallowUnknownFields()
			var r Request
			var data any
			var err error
			if e := decoder.Decode(&r); e != nil {
				err = errors.New("invalid request")
			} else {
				data, err = s.public(r)
			}
			reply := map[string]any{"data": data}
			if err != nil {
				reply = map[string]any{"error": err.Error()}
			}
			json.NewEncoder(c).Encode(reply)
		}()
	}
}

// The agent proxy permits only read/sign/session-binding requests. OpenSSH enforces destination
// and remote-user constraints cryptographically; no signing implementation lives in this binary.
func frame(r io.Reader) ([]byte, error) {
	var hdr [4]byte
	if _, e := io.ReadFull(r, hdr[:]); e != nil {
		return nil, e
	}
	n := binary.BigEndian.Uint32(hdr[:])
	if n < 1 || n > 262144 {
		return nil, errors.New("invalid agent frame")
	}
	b := make([]byte, n+4)
	copy(b, hdr[:])
	_, e := io.ReadFull(r, b[4:])
	return b, e
}
func permittedFrame(b []byte) bool {
	if len(b) < 5 {
		return false
	}
	switch b[4] {
	case 11, 13:
		return true
	case 27:
		if len(b) < 9 {
			return false
		}
		n := int(binary.BigEndian.Uint32(b[5:9]))
		return n == len("session-bind@openssh.com") && len(b) >= 9+n && string(b[9:9+n]) == "session-bind@openssh.com"
	}
	return false
}
func (s *Store) proxy(c *net.UnixConn, rec Record, upstream string) {
	defer c.Close()
	if uid, e := peerUID(c); e != nil || uid != uint32(s.Owner.UID) && uid != 0 {
		return
	}
	backend, e := net.Dial("unix", upstream)
	if e != nil {
		return
	}
	defer backend.Close()
	for {
		c.SetReadDeadline(time.Now().Add(5 * time.Minute))
		b, e := frame(c)
		if e != nil {
			return
		}
		current, approvalError := s.approved(rec.Provider, rec.ID)
		if approvalError != nil || current.Key != rec.Key || current.Host != rec.Host || current.Port != rec.Port || current.User != rec.User || current.HostKey != rec.HostKey {
			return
		}
		if !permittedFrame(b) {
			c.Write([]byte{0, 0, 0, 1, 5})
			continue
		}
		backend.SetDeadline(time.Now().Add(10 * time.Second))
		if _, e = backend.Write(b); e != nil {
			return
		}
		answer, e := frame(backend)
		if e != nil {
			return
		}
		if _, e = c.Write(answer); e != nil {
			return
		}
	}
}
func systemCommand(path string, args ...string) *exec.Cmd {
	cmd := exec.Command(path, args...)
	cmd.Env = []string{"PATH=/usr/bin:/bin:/usr/sbin:/sbin", "HOME=/var/root", "LANG=C", "LC_ALL=C"}
	return cmd
}
func validEndpoint(rec Record) bool {
	return idPattern.MatchString(rec.ID) && validProvider(rec.Provider) && rec.Port > 0 && rec.Port <= 65535 && regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9_.-]*$`).MatchString(rec.User) && safeLabel(rec.Host) && !strings.Contains(rec.Host, "%") && (net.ParseIP(rec.Host) != nil || regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9.-]*$`).MatchString(rec.Host))
}
func (s *Store) startSigner(rec Record) (func(), error) {
	if !validEndpoint(rec) {
		return nil, errors.New("invalid approved endpoint")
	}
	dir := publicDir(s.Run, rec.Provider, rec.ID)
	if e := os.MkdirAll(dir, 0755); e != nil {
		return nil, e
	}
	pub, e := os.ReadFile(rec.Key + ".pub")
	if e != nil {
		return nil, e
	}
	known, e := os.ReadFile(rec.KnownHosts)
	if e != nil {
		return nil, e
	}
	if e = atomic(filepath.Join(dir, "identity.pub"), pub, 0644); e != nil {
		return nil, e
	}
	if e = atomic(filepath.Join(dir, "known_hosts"), known, 0644); e != nil {
		return nil, e
	}
	internal := filepath.Join(s.Root, filepath.Base(publicDir(s.Run, rec.Provider, rec.ID))+".sock")
	os.Remove(internal)
	cmd := systemCommand("/usr/bin/ssh-agent", "-D", "-a", internal)
	if e = cmd.Start(); e != nil {
		return nil, e
	}
	cleanup := func() { cmd.Process.Kill(); cmd.Wait(); os.Remove(internal) }
	ready := false
	for n := 0; n < 100; n++ {
		if _, e = os.Stat(internal); e == nil {
			ready = true
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !ready {
		cleanup()
		return nil, errors.New("SSH signer did not start")
	}
	add := systemCommand("/usr/bin/ssh-add", "-H", rec.KnownHosts, "-h", rec.User+"@"+keyOf(rec.Provider, rec.ID), rec.Key)
	add.Env = append(add.Env, "SSH_AUTH_SOCK="+internal)
	if e = add.Run(); e != nil {
		cleanup()
		return nil, errors.New("cannot load destination-restricted key; OpenSSH 8.9+ is required")
	}
	listener, e := socketListener(filepath.Join(dir, "agent.sock"), s.Owner.UID, s.Owner.GID)
	if e != nil {
		cleanup()
		return nil, e
	}
	go func() {
		for {
			c, e := listener.AcceptUnix()
			if e != nil {
				return
			}
			go s.proxy(c, rec, internal)
		}
	}()
	return func() { listener.Close(); cleanup() }, nil
}
func (s *Store) daemon(ctx context.Context) error {
	if e := os.MkdirAll(s.Run, 0755); e != nil {
		return e
	}
	l, e := socketListener(filepath.Join(s.Run, "api.sock"), s.Owner.UID, s.Owner.GID)
	if e != nil {
		return e
	}
	defer l.Close()
	go s.serveAPI(l)
	type signer struct {
		stop      func()
		signature string
	}
	running := map[string]signer{}
	defer func() {
		for _, item := range running {
			item.stop()
		}
	}()
	for {
		db, e := s.readDB()
		if e != nil {
			return e
		}
		for key, current := range running {
			rec, exists := db.Records[key]
			data, _ := json.Marshal(rec)
			if !exists || rec.Revoked || current.signature != string(data) {
				current.stop()
				delete(running, key)
			}
		}
		for key, rec := range db.Records {
			if rec.Revoked {
				continue
			}
			if _, exists := running[key]; !exists {
				if stop, e := s.startSigner(rec); e == nil {
					data, _ := json.Marshal(rec)
					running[key] = signer{stop, string(data)}
				}
			}
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(time.Second):
		}
	}
}

func (s *Store) login(provider string) error {
	if !validProvider(provider) {
		return errors.New("invalid provider")
	}
	if e := s.Confirm(fmt.Sprintf("LINK/ROTATE %s credentials\nThe new API key will be stored root-only. Existing SSH grants for this provider will be revoked.", provider)); e != nil {
		return e
	}
	f, e := os.OpenFile("/dev/tty", os.O_RDWR, 0)
	if e != nil {
		return e
	}
	fmt.Fprintf(f, "%s API key (stored root-only, input hidden): ", provider)
	secret, e := term.ReadPassword(int(f.Fd()))
	fmt.Fprintln(f)
	f.Close()
	if e != nil {
		return errors.New("hidden terminal input unavailable")
	}
	token := strings.TrimSpace(string(secret))
	return s.linkToken(provider, token)
}

func (s *Store) linkToken(provider, token string) error {
	if !validProvider(provider) {
		return errors.New("invalid provider")
	}
	if len(token) < 1 || len(token) > 8192 || !regexp.MustCompile(`^[!-~]+$`).MatchString(token) {
		return errors.New("invalid token format")
	}
	unlock, e := s.lock()
	if e != nil {
		return e
	}
	defer unlock()
	tokenPath := filepath.Join(s.Root, provider+".token")
	previous, previousError := os.ReadFile(tokenPath)
	if e = atomic(tokenPath, []byte(token), 0600); e != nil {
		return e
	}
	if _, e = s.instances(provider); e != nil {
		if previousError == nil {
			atomic(tokenPath, previous, 0600)
		} else {
			os.Remove(tokenPath)
		}
		return errors.New("provider key validation failed; previous credentials were preserved")
	}
	db, e := s.readDB()
	if e != nil {
		return e
	}
	for key, rec := range db.Records {
		if rec.Provider == provider {
			rec.Revoked = true
			db.Records[key] = rec
		}
	}
	return writeJSON(filepath.Join(s.Root, "resources.json"), db)
}
func (s *Store) newKey(label string) (string, string, error) {
	key := filepath.Join(s.Root, "key-"+label)
	cmd := systemCommand("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "cloudrig", "-f", key)
	if e := cmd.Run(); e != nil {
		return "", "", errors.New("could not generate root-only key")
	}
	pub, e := os.ReadFile(key + ".pub")
	return key, strings.TrimSpace(string(pub)), e
}
func (s *Store) pin(rec *Record) error {
	if !validEndpoint(*rec) {
		return errors.New("SSH endpoint is not ready; approve this instance later")
	}
	cmd := systemCommand("/usr/bin/ssh-keyscan", "-T", "5", "-p", strconv.Itoa(rec.Port), "-t", "ed25519", rec.Host)
	data, e := cmd.Output()
	if e != nil {
		return errors.New("could not obtain host key; approve when SSH is ready")
	}
	alias := keyOf(rec.Provider, rec.ID)
	var known strings.Builder
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 3 && fields[1] == "ssh-ed25519" {
			fmt.Fprintf(&known, "%s %s %s\n", alias, fields[1], fields[2])
		}
	}
	if known.Len() == 0 {
		return errors.New("no Ed25519 host key available")
	}
	rec.HostKey = known.String()
	rec.KnownHosts = filepath.Join(s.Root, alias+".known_hosts")
	return atomic(rec.KnownHosts, []byte(known.String()), 0600)
}
func (s *Store) approve(provider, id string) error {
	if !validProvider(provider) || !idPattern.MatchString(id) {
		return errors.New("invalid resource")
	}
	i, e := s.instance(provider, id)
	if e != nil {
		return e
	}
	if !i.Direct {
		return errors.New("direct SSH is required")
	}
	if e = s.Confirm(fmt.Sprintf("Authorize SSH to %s/%s (%s) at %s:%d?", provider, id, i.Name, i.Host, i.Port)); e != nil {
		return e
	}
	unlock, e := s.lock()
	if e != nil {
		return e
	}
	defer unlock()
	db, e := s.readDB()
	if e != nil {
		return e
	}
	var key, pub string
	if previous, exists := db.Records[keyOf(provider, id)]; exists && previous.Key != "" {
		key = previous.Key
		bytes, err := os.ReadFile(key + ".pub")
		if err != nil {
			return err
		}
		pub = string(bytes)
	} else {
		if provider == "runpod" {
			return errors.New("existing RunPod has no protected per-pod key; create a crig rental instead of importing a user-readable private key")
		}
		key, pub, e = s.newKey(keyOf(provider, id) + "-" + strconv.FormatInt(time.Now().UnixNano(), 10))
		if e != nil {
			return e
		}
	}
	if provider == "vastai" {
		_, e = s.call(provider, "POST", "/api/v0/instances/"+id+"/ssh/", map[string]string{"ssh_key": pub})
		if e != nil {
			return e
		}
	}

	rec := Record{Provider: provider, ID: id, Name: i.Name, Host: i.Host, Port: i.Port, User: i.User, Key: key}
	if e = s.pin(&rec); e != nil {
		return e
	}
	db.Records[keyOf(provider, id)] = rec
	return writeJSON(filepath.Join(s.Root, "resources.json"), db)
}

type RentArgs struct {
	Provider, ID, Query, Name, Image string
	Disk, Count                      int
}

func (s *Store) rent(a RentArgs) (string, error) {
	if !validProvider(a.Provider) || !namePattern.MatchString(a.Name) || !regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,511}$`).MatchString(a.Image) {
		return "", errors.New("invalid rental configuration")
	}
	offers, e := s.offers(Request{Op: "offers", Provider: a.Provider, Query: a.Query, Disk: a.Disk, Count: a.Count})
	if e != nil {
		return "", e
	}
	var chosen *Offer
	for _, o := range offers {
		if o.ID == a.ID {
			copy := o
			chosen = &copy
			break
		}
	}
	if chosen == nil {
		return "", errors.New("offer is no longer available")
	}
	o := *chosen
	price := o.Hourly
	extra := " + storage (not quoted)"
	if o.DiskRate != nil {
		price += float64(a.Disk) * (*o.DiskRate)
		extra = " including selected disk"
	}
	if !validNumber(price) {
		return "", errors.New("invalid quote")
	}
	if e = s.Confirm(fmt.Sprintf("RENT %s\nProvider: %s\nGPU: %s x%d, %.1f GB each\nLocation: %s\nDisk: %d GB\nImage: %s\nCurrent quote: $%.6f/hour%s\nNo automatic shutdown. Billing begins on allocation.", a.Name, a.Provider, o.GPU, o.Count, o.VRAM, o.Region, a.Disk, a.Image, price, extra)); e != nil {
		return "", e
	}
	unlock, e := s.lock()
	if e != nil {
		return "", e
	}
	defer unlock()
	refreshed, refreshError := s.offers(Request{Op: "offers", Provider: a.Provider, Query: a.Query, Disk: a.Disk, Count: a.Count})
	if refreshError != nil {
		return "", refreshError
	}
	freshFound := false
	for _, current := range refreshed {
		if current.ID == a.ID {
			cost := current.Hourly
			if current.DiskRate != nil {
				cost += float64(a.Disk) * (*current.DiskRate)
			}
			changed := current.Count != o.Count || current.GPU != o.GPU || current.VRAM != o.VRAM || current.Region != o.Region || current.Cloud != o.Cloud
			if changed || current.DiskRate == nil && o.DiskRate != nil || !validNumber(cost) || cost > price+0.000001 {
				return "", errors.New("price or GPU conditions changed; obtain a new approval")
			}
			freshFound = true
			break
		}
	}
	if !freshFound {
		return "", errors.New("offer disappeared; obtain a new approval")
	}
	db, e := s.readDB()
	if e != nil {
		return "", e
	}
	pending := a.Provider + "/" + a.Name
	if db.Pending[pending] {
		return "", errors.New("a request with this name has an unknown outcome; inspect account before retrying")
	}
	for _, rec := range db.Records {
		if rec.Provider == a.Provider && rec.Name == a.Name {
			return "", errors.New("name already has a recorded allocation; inspect it before renting again")
		}
	}
	instances, e := s.instances(a.Provider)
	if e != nil {
		return "", e
	}
	for _, i := range instances {
		if i.Name == a.Name {
			return "", errors.New("name already rented")
		}
	}
	key, pub, e := s.newKey(a.Provider + "-" + a.Name + "-" + strconv.FormatInt(time.Now().UnixNano(), 10))
	if e != nil {
		return "", e
	}
	db.Pending[pending] = true
	if e = writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
		return "", e
	}
	var data any
	if a.Provider == "vastai" {
		data, e = s.call("vastai", "PUT", "/api/v0/asks/"+a.ID+"/", map[string]any{"client_id": "me", "image": a.Image, "disk": a.Disk, "label": a.Name, "runtype": "ssh_direct", "cancel_unavail": true})
	} else {
		parts := strings.Split(a.ID, "|")
		if len(parts) != 3 {
			return "", errors.New("invalid RunPod offer")
		}
		data, e = s.call("runpod", "POST", "/v2/pods", map[string]any{"name": a.Name, "image": a.Image, "disk": a.Disk, "gpu": map[string]any{"id": parts[0], "count": o.Count}, "cloud": parts[1], "dataCenterIds": []string{parts[2]}, "ports": []string{"22/tcp"}, "startSsh": true, "env": map[string]string{"PUBLIC_KEY": pub}})
	}
	if e != nil {
		return "", e
	}
	id := text(object(data)["id"])
	if a.Provider == "vastai" {
		id = text(object(data)["new_contract"])
	}
	if !idPattern.MatchString(id) {
		return "", errors.New("allocation outcome unknown; inspect rented instances")
	}
	rec := Record{Provider: a.Provider, ID: id, Name: a.Name, Key: key}
	db.Records[keyOf(a.Provider, id)] = rec
	db.Pending[pending] = false
	if e = writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
		return id, e
	}
	if a.Provider == "vastai" {
		if _, e = s.call("vastai", "POST", "/api/v0/instances/"+id+"/ssh/", map[string]string{"ssh_key": pub}); e != nil {
			return id, e
		}
	}
	fmt.Fprintf(os.Stderr, "Allocated %s. Waiting for SSH endpoint; the instance is billable.\n", id)
	for n := 0; n < 100; n++ {
		i, err := s.instance(a.Provider, id)
		if err == nil && i.Host != "" && i.Port > 0 && i.Direct {
			rec.Host = i.Host
			rec.Port = i.Port
			rec.User = i.User
			if e = s.pin(&rec); e == nil {
				db.Records[keyOf(a.Provider, id)] = rec
				return id, writeJSON(filepath.Join(s.Root, "resources.json"), db)
			}
		}
		time.Sleep(3 * time.Second)
	}
	return id, errors.New("rented but SSH was not ready; inspect account and use sudo approve later")
}
func (s *Store) resumeQuote(provider, id string) (float64, error) {
	if provider == "vastai" {
		i, e := s.instance(provider, id)
		if e != nil {
			return 0, e
		}
		if i.ContractRate <= 0 || !validNumber(i.ContractRate) {
			return 0, errors.New("existing contract has no verifiable compute quote; refusing billable start")
		}
		return i.ContractRate, nil
	}
	raw, e := s.call("runpod", "GET", "/v2/pods/"+id, nil)
	if e != nil {
		return 0, e
	}
	pod := object(raw)
	gpu := object(pod["gpu"])
	count := num(gpu["count"])
	if !validNumber(count) || count < 1 || count > 64 || math.Trunc(count) != count {
		return 0, errors.New("cannot verify the pod GPU count; refusing billable start")
	}
	cloud := strings.ToLower(text(pod["cloud"]))
	if cloud != "secure" && cloud != "community" {
		return 0, errors.New("unknown pod cloud tier")
	}
	catalog, e := s.call("runpod", "GET", "/v2/catalog/gpus", nil)
	if e != nil {
		return 0, e
	}
	for _, entry := range array(object(catalog)["gpus"]) {
		row := object(entry)
		if text(row["id"]) == text(gpu["id"]) {
			price := num(object(row["price"])[cloud]) * count
			if validNumber(price) && price > 0 {
				return price, nil
			}
		}
	}
	return 0, errors.New("cannot establish the current compute quote for this pod")
}

func (s *Store) mutate(provider, id, action string) error {
	if !idPattern.MatchString(id) || !validProvider(provider) {
		return errors.New("invalid resource")
	}
	i, e := s.instance(provider, id)
	if e != nil {
		return e
	}
	if action != "start" && action != "destroy" {
		return errDenied
	}
	message := fmt.Sprintf("%s %s/%s (%s)", strings.ToUpper(action), provider, id, i.Name)
	quoted := 0.0
	if action == "start" {
		quoted, e = s.resumeQuote(provider, id)
		if e != nil {
			return e
		}
		message += fmt.Sprintf("\nCurrent compute quote: $%.6f/hour. Storage is billed separately.", quoted)
	} else {
		message += "\nInstance-local files will be deleted."
	}

	if e = s.Confirm(message); e != nil {
		return e
	}
	unlock, e := s.lock()
	if e != nil {
		return e
	}
	defer unlock()
	db, e := s.readDB()
	if e != nil {
		return e
	}
	if action == "start" {
		fresh, e := s.resumeQuote(provider, id)
		if e != nil {
			return e
		}
		if fresh > quoted+0.000001 {
			return errors.New("resume price increased; obtain a new approval")
		}
	}
	if action == "destroy" {
		if rec, ok := db.Records[keyOf(provider, id)]; ok {
			rec.Revoked = true
			db.Records[keyOf(provider, id)] = rec
			if e = writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
				return e
			}
		}
	}
	_, e = s.transition(provider, id, action)
	if e != nil {
		return e
	}
	if action == "destroy" {
		delete(db.Records, keyOf(provider, id))
		return writeJSON(filepath.Join(s.Root, "resources.json"), db)
	}
	if action == "start" {
		if rec, ok := db.Records[keyOf(provider, id)]; ok && !rec.Revoked {
			for n := 0; n < 100; n++ {
				current, err := s.instance(provider, id)
				if err == nil && current.Host != "" && current.Port > 0 && current.Direct {
					rec.Host = current.Host
					rec.Port = current.Port
					rec.User = current.User
					if err = s.pin(&rec); err == nil {
						db.Records[keyOf(provider, id)] = rec
						return writeJSON(filepath.Join(s.Root, "resources.json"), db)
					}
				}
				time.Sleep(3 * time.Second)
			}
			return errors.New("start accepted but SSH was not ready; approve it later")
		}
	}
	return nil
}

// This test is run after real root installation, not simulated by the unprivileged unit suite.
func (s *Store) selfTest() error {
	probe := filepath.Join(s.Root, ".permission-probe")
	if e := atomic(probe, []byte("not-a-real-credential"), 0600); e != nil {
		return e
	}
	defer os.Remove(probe)
	command := systemCommand("/bin/cat", probe)
	command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: uint32(s.Owner.UID), Gid: uint32(s.Owner.GID)}}
	if e := command.Run(); e == nil {
		return errors.New("credential isolation failed: ordinary user could read a root-only file")
	}
	validate := systemCommand("/usr/bin/sudo", "-n", "-v")
	validate.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: uint32(s.Owner.UID), Gid: uint32(s.Owner.GID)}}
	if e := validate.Run(); e == nil {
		return errors.New("sudo can be validated non-interactively; disable passwordless grants and credential caching")
	}
	return nil
}

func main() {
	if os.Geteuid() != 0 {
		fmt.Fprintln(os.Stderr, "cloudrig-admin must run as root via freshly authenticated sudo")
		os.Exit(1)
	}
	invokingUID := os.Getenv("SUDO_UID")
	os.Clearenv()
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: cloudrig-admin daemon|login|rent|approve|start|destroy")
		os.Exit(1)
	}
	var cfg Config
	data, e := os.ReadFile(stateRoot + "/owner.json")
	if e == nil {
		e = json.Unmarshal(data, &cfg)
	}
	if e != nil || cfg.UID < 1 {
		fmt.Fprintln(os.Stderr, "root-owned installation is not initialized")
		os.Exit(1)
	}
	s := newStore(stateRoot, runRoot, cfg)
	verb := os.Args[1]
	args := os.Args[2:]
	if verb != "daemon" {
		uid, _ := strconv.Atoi(invokingUID)
		if uid != cfg.UID {
			fmt.Fprintln(os.Stderr, "invoke from the configured user's own sudo session")
			os.Exit(1)
		}
	}
	var output any = map[string]bool{"ok": true}
	switch verb {
	case "daemon":
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		e = s.daemon(ctx)
	case "self-test":
		if len(args) != 0 {
			e = errors.New("self-test takes no arguments")
		} else {
			e = s.selfTest()
		}
	case "login":
		if len(args) != 1 {
			e = errors.New("provider required")
		} else {
			e = s.login(args[0])
		}
	case "approve", "start", "destroy":
		if len(args) != 2 {
			e = errors.New("provider and ID required")
		} else if verb == "approve" {
			e = s.approve(args[0], args[1])
		} else {
			e = s.mutate(args[0], args[1], verb)
		}
	case "rent":
		if len(args) != 7 {
			e = errors.New("invalid rental arguments")
		} else {
			disk, _ := strconv.Atoi(args[5])
			count, _ := strconv.Atoi(args[6])
			var id string
			id, e = s.rent(RentArgs{args[0], args[1], args[2], args[3], args[4], disk, count})
			output = map[string]string{"id": id}
		}
	default:
		e = errDenied
	}
	if e != nil {
		fmt.Fprintln(os.Stderr, "cloudrig-admin:", e)
		os.Exit(1)
	}
	json.NewEncoder(os.Stdout).Encode(output)
}
