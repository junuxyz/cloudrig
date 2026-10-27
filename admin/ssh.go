package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

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
	if rec, e := s.approved(i.Provider, i.ID); e == nil {
		i.Approved = true
		if rec.Connecting || !validEndpoint(rec) {
			return i
		}
		dir := publicDir(s.Run, i.Provider, i.ID)
		i.PublicKey = filepath.Join(dir, "identity.pub")
		i.AgentSocket = filepath.Join(dir, "agent.sock")
		i.KnownHosts = filepath.Join(dir, "known_hosts")
		i.Host = rec.Host
		i.Port = rec.Port
		i.User = rec.User
	}
	return i
}

func (s *Store) finishConnection(i Instance) error {
	if i.Status != "running" || !i.Direct || i.Host == "" || i.Port == 0 {
		return nil
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
	key := keyOf(i.Provider, i.ID)
	rec, exists := db.Records[key]
	if !exists || rec.Revoked || !rec.Connecting || rec.Key == "" {
		return nil
	}
	rec.Host, rec.Port, rec.User = i.Host, i.Port, i.User
	// SSH may still be booting. Keep the authorized connection pending until a later read.
	if e = s.pin(&rec); e != nil {
		return nil
	}
	if i.Provider == "vastai" {
		pub, e := os.ReadFile(rec.Key + ".pub")
		if e != nil {
			return e
		}
		if _, e = s.call(i.Provider, "POST", "/api/v0/instances/"+i.ID+"/ssh/", map[string]string{"ssh_key": strings.TrimSpace(string(pub))}); e != nil {
			return fmt.Errorf("instance %s is rented; retry its connection to finish SSH setup: %w", i.ID, e)
		}
	}
	rec.Connecting = false
	db.Records[key] = rec
	return writeJSON(filepath.Join(s.Root, "resources.json"), db)
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
		if approvalError != nil || current.Connecting || current.Key != rec.Key || current.Host != rec.Host || current.Port != rec.Port || current.User != rec.User || current.HostKey != rec.HostKey {
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
