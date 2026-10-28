package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"golang.org/x/crypto/ssh"
)

type sshFixture struct {
	port    int
	hostKey []byte
}

func newSSHFixture(t *testing.T, authorized []byte) sshFixture {
	t.Helper()
	var allowed ssh.PublicKey
	if len(authorized) != 0 {
		var e error
		allowed, _, _, _, e = ssh.ParseAuthorizedKey(authorized)
		if e != nil {
			t.Fatal(e)
		}
	}
	_, private, e := ed25519.GenerateKey(rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	host, e := ssh.NewSignerFromKey(private)
	if e != nil {
		t.Fatal(e)
	}
	cfg := &ssh.ServerConfig{PublicKeyCallback: func(_ ssh.ConnMetadata, key ssh.PublicKey) (*ssh.Permissions, error) {
		if allowed == nil || bytes.Equal(key.Marshal(), allowed.Marshal()) {
			return &ssh.Permissions{}, nil
		}
		return nil, fmt.Errorf("fixture refuses unknown public key")
	}}
	cfg.AddHostKey(host)
	listener, e := net.Listen("tcp", "127.0.0.1:0")
	if e != nil {
		t.Fatal(e)
	}
	var workers sync.WaitGroup
	var connections sync.Map
	finished := make(chan struct{})
	go func() {
		defer close(finished)
		for {
			c, e := listener.Accept()
			if e != nil {
				return
			}
			connections.Store(c, true)
			workers.Add(1)
			go func() {
				defer workers.Done()
				defer connections.Delete(c)
				defer c.Close()
				c.SetDeadline(time.Now().Add(5 * time.Second))
				transport, channels, requests, e := ssh.NewServerConn(c, cfg)
				if e != nil {
					return
				}
				defer transport.Close()
				go ssh.DiscardRequests(requests)
				for channel := range channels {
					if channel.ChannelType() != "session" {
						channel.Reject(ssh.UnknownChannelType, "session only")
						continue
					}
					session, requests, e := channel.Accept()
					if e != nil {
						return
					}
					for request := range requests {
						if request.Type != "exec" {
							request.Reply(false, nil)
							continue
						}
						request.Reply(true, nil)
						io.WriteString(session, "authenticated\n")
						session.SendRequest("exit-status", false, ssh.Marshal(struct{ Status uint32 }{0}))
						session.Close()
						break
					}
				}
			}()
		}
	}()
	t.Cleanup(func() {
		listener.Close()
		<-finished
		connections.Range(func(k, v any) bool { k.(net.Conn).Close(); return true })
		workers.Wait()
	})
	return sshFixture{listener.Addr().(*net.TCPAddr).Port, ssh.MarshalAuthorizedKey(host.PublicKey())}
}

func constrainedSigner(t *testing.T) (*Store, Record, string, sshFixture, sshFixture) {
	t.Helper()
	s, rec, approved, other := signerRecord(t)
	stop, e := s.startSigner(rec)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(stop)
	socket := filepath.Join(publicDir(s.Run, "vastai", "authority"), "agent.sock")
	return s, rec, socket, approved, other
}

func signerRecord(t *testing.T) (*Store, Record, sshFixture, sshFixture) {
	t.Helper()
	s := testStore(t)
	key := filepath.Join(s.Root, "authority-key")
	if out, e := exec.Command("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key).CombinedOutput(); e != nil {
		t.Fatalf("fixture key: %s %v", out, e)
	}
	public, e := os.ReadFile(key + ".pub")
	if e != nil {
		t.Fatal(e)
	}
	// Both servers accept this key for every username. Refusal must come from the constrained signer.
	approved := newSSHFixture(t, public)
	other := newSSHFixture(t, public)
	known := filepath.Join(s.Root, "known")
	data := append([]byte("vastai-authority "), approved.hostKey...)
	data = append(data, []byte("different-host ")...)
	data = append(data, other.hostKey...)
	if e = os.WriteFile(known, data, 0600); e != nil {
		t.Fatal(e)
	}
	rec := Record{
		Provider:   "vastai",
		ID:         "authority",
		Name:       "fixture",
		User:       "worker",
		Host:       "127.0.0.1",
		Port:       approved.port,
		Key:        key,
		KnownHosts: known,
		HostKey:    string(approved.hostKey),
	}
	if e = writeJSON(filepath.Join(s.Root, "resources.json"), DB{Records: map[string]Record{"vastai-authority": rec}}); e != nil {
		t.Fatal(e)
	}
	return s, rec, approved, other
}
func sshAuthentication(key, agent, known, alias, user string, port int) (string, error) {
	cmd := exec.Command("/usr/bin/ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent="+agent, "-i", key, "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile="+known, "-o", "GlobalKnownHostsFile=/dev/null", "-o", "HostKeyAlias="+alias, "-p", strconv.Itoa(port), user+"@127.0.0.1", "fixture-command")
	out, e := cmd.CombinedOutput()
	return string(out), e
}
func TestSSHConstraintsRejectOtherHostAndUserThatAcceptTheSameKey(t *testing.T) {
	_, rec, socket, approved, other := constrainedSigner(t)
	cases := []struct {
		name, user, alias string
		port              int
		allowed           bool
	}{
		{"approved", "worker", "vastai-authority", approved.port, true},
		{"wrong host", "worker", "different-host", other.port, false},
		{"wrong user", "another-user", "vastai-authority", approved.port, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if out, e := sshAuthentication(rec.Key, "none", rec.KnownHosts, tc.alias, tc.user, tc.port); e != nil || !strings.Contains(out, "authenticated\n") {
				t.Fatal("control server did not accept fixture identity", out, e)
			}
			out, e := sshAuthentication(rec.Key+".pub", socket, rec.KnownHosts, tc.alias, tc.user, tc.port)
			if tc.allowed {
				if e != nil || out != "authenticated\n" {
					t.Fatal("approved signer authentication failed", out, e)
				}
			} else if e == nil || strings.Contains(out, "authenticated\n") {
				t.Fatal("destination/user constraint bypassed", out, e)
			}
		})
	}
}
func TestOpenSignerConnectionsLoseAuthorityImmediatelyOnRecordChanges(t *testing.T) {
	s, rec, socket, _, _ := constrainedSigner(t)
	for _, change := range []string{"revoke", "remove", "replace-host-key"} {
		t.Run(change, func(t *testing.T) {
			original := DB{Records: map[string]Record{"vastai-authority": rec}}
			if e := writeJSON(filepath.Join(s.Root, "resources.json"), original); e != nil {
				t.Fatal(e)
			}
			c, e := net.Dial("unix", socket)
			if e != nil {
				t.Fatal(e)
			}
			defer c.Close()
			c.SetDeadline(time.Now().Add(2 * time.Second))
			identities := []byte{0, 0, 0, 1, 11}
			if _, e = c.Write(identities); e != nil {
				t.Fatal(e)
			}
			response, e := frame(c)
			if e != nil || len(response) < 9 || response[4] != 12 {
				t.Fatal("open connection control failed", response, e)
			}
			altered := rec
			switch change {
			case "revoke":
				altered.Revoked = true
			case "replace-host-key":
				altered.HostKey = "different-host-key"
			}
			next := DB{Records: map[string]Record{"vastai-authority": altered}}
			if change == "remove" {
				next.Records = map[string]Record{}
			}
			if e = writeJSON(filepath.Join(s.Root, "resources.json"), next); e != nil {
				t.Fatal(e)
			}
			c.Write(identities)
			response, e = frame(c)
			if timeout, ok := e.(net.Error); ok && timeout.Timeout() {
				t.Fatal("revoked connection hung")
			}
			if e == nil || len(response) != 0 {
				t.Fatal("existing socket retained signing authority", response, e)
			}
		})
	}
}
