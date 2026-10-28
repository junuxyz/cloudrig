package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func testStore(t *testing.T) *Store {
	t.Helper()
	root, e := os.MkdirTemp("/tmp", "cg-admin-")
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { os.RemoveAll(root) })
	run := filepath.Join(root, "run")
	os.Mkdir(run, 0700)
	s := newStore(root, run, Config{os.Getuid(), os.Getgid()})
	if e := os.WriteFile(filepath.Join(root, "vastai.token"), []byte("ROOT_SECRET_MARKER"), 0600); e != nil {
		t.Fatal(e)
	}
	if e := os.WriteFile(filepath.Join(root, "runpod.token"), []byte("ROOT_SECRET_MARKER"), 0600); e != nil {
		t.Fatal(e)
	}
	return s
}

func TestPublicAPICannotStopCompute(t *testing.T) {
	s := testStore(t)
	if _, e := s.public(Request{Op: "stop", Provider: "vastai", ID: "1"}); e == nil {
		t.Fatal("unprivileged stop allowed")
	}
}

func TestProviderErrorsAreSanitized(t *testing.T) {
	s := testStore(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(401)
		io.WriteString(w, "ROOT_SECRET_MARKER")
	}))
	defer server.Close()
	s.Base["runpod"] = server.URL
	_, e := s.instances("runpod")
	if e == nil || strings.Contains(e.Error(), "ROOT_SECRET") {
		t.Fatal("unsafe error", e)
	}
}
func TestNoCredentialForwardingRedirect(t *testing.T) {
	s := testStore(t)
	hit := false
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { hit = true }))
	defer target.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, 307) }))
	defer server.Close()
	s.Base["runpod"] = server.URL
	if _, e := s.instances("runpod"); e == nil || hit {
		t.Fatal("redirect followed")
	}
}
func TestAgentProtocolRejectsMutationAndUnknownExtensions(t *testing.T) {
	for _, kind := range []byte{7, 8, 9, 17, 18, 19, 20, 22, 23, 25, 26} {
		if permittedFrame([]byte{0, 0, 0, 1, kind}) {
			t.Fatal("mutable agent operation allowed", kind)
		}
	}
	bad := append([]byte{0, 0, 0, 20, 27, 0, 0, 0, 11}, []byte("query@evil!")...)
	if permittedFrame(bad) {
		t.Fatal("unapproved extension allowed")
	}
	for _, kind := range []byte{11, 13} {
		if !permittedFrame([]byte{0, 0, 0, 1, kind}) {
			t.Fatal("read/sign request rejected")
		}
	}
}
func TestOversizedAgentFrameRejected(t *testing.T) {
	for _, size := range []uint32{0, 262145, 0xffffffff} {
		hdr := make([]byte, 4)
		binary.BigEndian.PutUint32(hdr, size)
		if _, e := frame(bytes.NewReader(hdr)); e == nil {
			t.Fatal("bad length allowed")
		}
	}
}
func TestEndpointRejectsScopedIPv6AndConfigInjection(t *testing.T) {
	r := Record{Provider: "vastai", ID: "1", Host: "203.0.113.1", User: "root", Port: 22}
	if !validEndpoint(r) {
		t.Fatal("valid endpoint rejected")
	}
	for _, host := range []string{"host\nMatch exec evil", "fe80::1%eth0\nMatch exec evil", "-oProxyCommand=evil"} {
		r.Host = host
		if validEndpoint(r) {
			t.Fatal("bad endpoint allowed")
		}
	}
}

func TestNativeAgentCannotExportOrPerformUnboundSignatures(t *testing.T) {
	s := testStore(t)
	key := filepath.Join(s.Root, "test-key")
	if e := exec.Command("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key).Run(); e != nil {
		t.Fatal(e)
	}
	host := filepath.Join(s.Root, "host-key")
	if e := exec.Command("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", host).Run(); e != nil {
		t.Fatal(e)
	}
	pub, _ := os.ReadFile(host + ".pub")
	known := filepath.Join(s.Root, "known")
	os.WriteFile(known, append([]byte("vastai-1 "), pub...), 0600)
	rec := Record{
		Provider:   "vastai",
		ID:         "1",
		User:       "root",
		Host:       "203.0.113.1",
		Port:       22,
		Key:        key,
		KnownHosts: known,
	}
	writeJSON(filepath.Join(s.Root, "resources.json"), DB{Records: map[string]Record{"vastai-1": rec}, Pending: map[string]bool{}})
	stop, e := s.startSigner(rec)
	if e != nil {
		t.Fatal(e)
	}
	defer stop()
	proxy := filepath.Join(publicDir(s.Run, "vastai", "1"), "agent.sock")
	cmd := exec.Command("/usr/bin/ssh-add", "-L")
	cmd.Env = append(os.Environ(), "SSH_AUTH_SOCK="+proxy)
	out, e := cmd.CombinedOutput()
	if e != nil || !bytes.Contains(out, []byte("ssh-ed25519")) ||
		bytes.Contains(out, []byte("PRIVATE KEY")) {
		t.Fatalf("public identities: %s %v", out, e)
	}
	for _, args := range [][]string{{"-T", key + ".pub"}, {"-D"}, {key}} {
		cmd = exec.Command("/usr/bin/ssh-add", args...)
		cmd.Env = append(os.Environ(), "SSH_AUTH_SOCK="+proxy)
		if _, e = cmd.CombinedOutput(); e == nil {
			t.Fatal("unbound signing or mutation succeeded", args)
		}
	}
	c, e := net.Dial("unix", proxy)
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(time.Second))
	c.Write([]byte{0, 0, 0, 1, 18})
	reply, e := frame(c)
	if e != nil || len(reply) != 5 || reply[4] != 5 {
		t.Fatal("mutation was not denied", reply, e)
	}
}

func TestApprovedSSHAuthenticationThroughProxy(t *testing.T) {
	if _, e := os.Stat("/usr/sbin/sshd"); e != nil {
		t.Skip("sshd not installed")
	}
	s := testStore(t)
	key := filepath.Join(s.Root, "client")
	host := filepath.Join(s.Root, "host")
	for _, path := range []string{key, host} {
		if e := exec.Command("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", path).Run(); e != nil {
			t.Fatal(e)
		}
	}
	listener, e := net.Listen("tcp", "127.0.0.1:0")
	if e != nil {
		t.Fatal(e)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()
	userBytes, e := exec.Command("/usr/bin/id", "-un").Output()
	if e != nil {
		t.Fatal(e)
	}
	username := strings.TrimSpace(string(userBytes))
	config := fmt.Sprintf("Port %d\nListenAddress 127.0.0.1\nHostKey %s\nPidFile %s\nAuthorizedKeysFile %s.pub\nStrictModes no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\nPermitUserRC no\nAllowUsers %s\n", port, host, filepath.Join(s.Root, "sshd.pid"), key, username)
	cfg := filepath.Join(s.Root, "sshd_config")
	os.WriteFile(cfg, []byte(config), 0600)
	daemon := exec.Command("/usr/sbin/sshd", "-D", "-e", "-f", cfg)
	var logs bytes.Buffer
	daemon.Stderr = &logs
	if e = daemon.Start(); e != nil {
		t.Fatal(e)
	}
	defer func() { daemon.Process.Kill(); daemon.Wait() }()
	ready := false
	for n := 0; n < 100; n++ {
		c, e := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 20*time.Millisecond)
		if e == nil {
			c.Close()
			ready = true
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !ready {
		t.Fatalf("fixture sshd did not start: %s", logs.String())
	}
	known := filepath.Join(s.Root, "known")
	hostPub, _ := os.ReadFile(host + ".pub")
	os.WriteFile(known, append([]byte("vastai-ssh-test "), hostPub...), 0600)
	rec := Record{
		Provider:   "vastai",
		ID:         "ssh-test",
		User:       username,
		Host:       "127.0.0.1",
		Port:       port,
		Key:        key,
		KnownHosts: known,
	}
	writeJSON(filepath.Join(s.Root, "resources.json"), DB{Records: map[string]Record{"vastai-ssh-test": rec}, Pending: map[string]bool{}})
	stop, e := s.startSigner(rec)
	if e != nil {
		t.Fatal(e)
	}
	defer stop()
	socket := filepath.Join(publicDir(s.Run, "vastai", "ssh-test"), "agent.sock")
	cmd := exec.Command("/usr/bin/ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent="+socket, "-i", key+".pub", "-o", "HostKeyAlias=vastai-ssh-test", "-o", "UserKnownHostsFile="+known, "-o", "GlobalKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=yes", "-p", strconv.Itoa(port), username+"@127.0.0.1", "printf approved-authentication")
	output, e := cmd.CombinedOutput()
	if e != nil {
		t.Fatalf("approved SSH failed: %s (%v); server: %s", output, e, logs.String())
	}
	if string(output) != "approved-authentication" {
		t.Fatal(string(output))
	}
	t.Run("reauthorize protected RunPod key after credential rotation", func(t *testing.T) {
		revoked := Record{Provider: "runpod", ID: "ssh-test", Key: key, Revoked: true}
		db, e := s.readDB()
		if e != nil {
			t.Fatal(e)
		}
		db.Records["runpod-ssh-test"] = revoked
		if e = writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
			t.Fatal(e)
		}
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method != "GET" || r.URL.Path != "/v2/pods/ssh-test" {
				t.Errorf("unexpected provider operation %s %s", r.Method, r.URL.Path)
				w.WriteHeader(500)
				return
			}
			json.NewEncoder(w).Encode(map[string]any{
				"id":   "ssh-test",
				"name": "fixture",
				"cost": 0.5,
				"ssh":  map[string]any{"direct": map[string]any{"host": "127.0.0.1", "port": port, "username": username}},
			})
		}))
		defer server.Close()
		s.Base["runpod"] = server.URL
		if e = s.approve("runpod", "ssh-test"); e != nil {
			t.Fatal(e)
		}
		restored, e := s.approved("runpod", "ssh-test")
		if e != nil || restored.Key != key || restored.HostKey == "" {
			t.Fatal("explicit approval did not restore the protected connection", e)
		}
	})
}

func mockVastQuotes(t *testing.T, s *Store, change string) *int {
	t.Helper()
	calls := new(int)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v0/gpu_names/unique/":
			io.WriteString(w, `{"gpu_names":["RTX 5090"]}`)
		case "/api/v0/bundles/":
			*calls++
			var query map[string]any
			if e := json.NewDecoder(r.Body).Decode(&query); e != nil {
				t.Error(e)
			}
			id := 1
			if *calls > 1 && object(query["ask_contract_id"])["eq"] != float64(1) {
				id = 2
			}
			price := 0.8
			region := "US"
			if change == "price" && *calls > 1 {
				price = 1.8
			}
			if change == "region" && *calls > 1 {
				region = "KR"
			}
			fmt.Fprintf(w, `{"offers":[{"id":%d,"gpu_name":"RTX 5090","num_gpus":2,"gpu_ram":32768,"dph_base":%f,"dph_total":%f,"storage_cost":0.72,"disk_space":100,"geolocation":%q}]}`, id, price, price+0.04, region)
		case "/api/v1/instances/":
			io.WriteString(w, `{"instances":[]}`)
		case "/api/v0/asks/1/":
			if r.Method != "PUT" {
				t.Error("wrong allocation method", r.Method)
			}
			io.WriteString(w, `{"new_contract":101}`)
		default:
			t.Errorf("unexpected provider operation %s %s", r.Method, r.URL.Path)
			w.WriteHeader(500)
		}
	}))
	t.Cleanup(server.Close)
	s.Base["vastai"] = server.URL
	return calls
}

func TestRentalRevalidatesSelectedVastAskWhenBundleRepresentativeChanges(t *testing.T) {
	s := testStore(t)
	calls := mockVastQuotes(t, s, "")
	offers, e := s.offers(Request{Provider: "vastai", Query: "5090", Disk: 40, Count: 1})
	if e != nil || len(offers) != 1 {
		t.Fatal("market search failed", offers, e)
	}
	var output bytes.Buffer
	s.Output = &output
	id, e := s.rent(RentArgs{"vastai", offers[0].ID, offers[0].GPU, "training", "pytorch/pytorch:tag", 40, offers[0].Count, nil})
	if e != nil || id != "101" || !strings.Contains(output.String(), "$0.840000/hour") || *calls != 3 {
		t.Fatal("selected ask lost during grouped market refresh", output.String(), *calls, e)
	}
}

func TestChangedQuoteIsRejectedBeforeAllocation(t *testing.T) {
	for _, change := range []string{"price", "region"} {
		t.Run(change, func(t *testing.T) {
			s := testStore(t)
			calls := mockVastQuotes(t, s, change)
			if _, e := s.rent(RentArgs{"vastai", "1", "5090", "training", "pytorch/pytorch:tag", 40, 1, nil}); e == nil || !strings.Contains(e.Error(), "changed") {
				t.Fatal(e)
			}
			if *calls != 2 {
				t.Fatal("quote not independently refreshed")
			}
			entries, _ := os.ReadDir(s.Root)
			for _, entry := range entries {
				if strings.HasPrefix(entry.Name(), "key-") {
					t.Fatal("key generated before final validated quote")
				}
			}
		})
	}
}
