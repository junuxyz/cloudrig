package main

import (
	"bytes"
	"context"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestExistingGrantSurvivesRuntimeDirectoryMove(t *testing.T) {
	s, rec, _, _ := signerRecord(t)
	journal := filepath.Join(s.Root, "resources.json")
	before, e := os.ReadFile(journal)
	if e != nil {
		t.Fatal(e)
	}
	moved := newStore(s.Root, filepath.Join(s.Root, "new-run"), s.Owner)
	stop, e := moved.startSigner(rec)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(stop)
	grant := moved.publicGrant(Instance{Provider: rec.Provider, ID: rec.ID})
	if !strings.HasPrefix(grant.AgentSocket, moved.Run+string(os.PathSeparator)) {
		t.Fatal("grant still uses the old runtime directory", grant.AgentSocket)
	}
	out, e := sshAuthentication(grant.PublicKey, grant.AgentSocket, grant.KnownHosts,
		keyOf(rec.Provider, rec.ID), grant.User, grant.Port)
	if e != nil || out != "authenticated\n" {
		t.Fatal("existing key cannot authenticate after runtime move", out, e)
	}
	after, e := os.ReadFile(journal)
	if e != nil || !bytes.Equal(before, after) {
		t.Fatal("runtime move changed the allocation journal", e)
	}
}

func awaitCondition(t *testing.T, label string, condition func() bool) {
	t.Helper()
	timeout := time.NewTimer(5 * time.Second)
	defer timeout.Stop()
	poll := time.NewTicker(10 * time.Millisecond)
	defer poll.Stop()
	for {
		if condition() {
			return
		}
		select {
		case <-timeout.C:
			t.Fatalf("timed out waiting for %s", label)
		case <-poll.C:
		}
	}
}
func signerResponds(socket string) bool {
	c, e := net.DialTimeout("unix", socket, 50*time.Millisecond)
	if e != nil {
		return false
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(100 * time.Millisecond))
	if _, e = c.Write([]byte{0, 0, 0, 1, 11}); e != nil {
		return false
	}
	response, e := frame(c)
	return e == nil && len(response) > 9 && response[4] == 12
}
func TestServiceReconcilesRevocationAndCleansUpOnShutdownOrCorruptState(t *testing.T) {
	for _, corrupt := range []bool{false, true} {
		name := "shutdown"
		if corrupt {
			name = "corrupt database"
		}
		t.Run(name, func(t *testing.T) {
			s, rec, _, _ := signerRecord(t)
			ctx, cancel := context.WithCancel(context.Background())
			done := make(chan struct{})
			result := make(chan error, 1)
			go func() { result <- s.daemon(ctx); close(done) }()
			t.Cleanup(func() {
				cancel()
				select {
				case <-done:
				case <-time.After(5 * time.Second):
					t.Error("service did not stop")
				}
			})
			agent := filepath.Join(publicDir(s.Run, rec.Provider, rec.ID), "agent.sock")
			awaitCondition(t, "initial signer", func() bool { return signerResponds(agent) })
			rec.Revoked = true
			db := DB{Records: map[string]Record{"vastai-authority": rec}}
			if e := writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
				t.Fatal(e)
			}
			awaitCondition(t, "revoked signer socket removal", func() bool { _, e := os.Stat(agent); return os.IsNotExist(e) })
			rec.Revoked = false
			db.Records["vastai-authority"] = rec
			if e := writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
				t.Fatal(e)
			}
			awaitCondition(t, "reapproved signer", func() bool { return signerResponds(agent) })
			if corrupt {
				if e := atomic(filepath.Join(s.Root, "resources.json"), []byte("{invalid json"), 0600); e != nil {
					t.Fatal(e)
				}
			} else {
				cancel()
			}
			select {
			case <-done:
			case <-time.After(5 * time.Second):
				t.Fatal("service failed to stop")
			}
			e := <-result
			if corrupt && e == nil || !corrupt && e != nil {
				t.Fatal("incorrect service exit result", e)
			}
			internal := filepath.Join(s.Root, filepath.Base(publicDir(s.Run, rec.Provider, rec.ID))+".sock")
			for _, path := range []string{agent, internal, filepath.Join(s.Run, "api.sock")} {
				if _, e := os.Stat(path); !os.IsNotExist(e) {
					t.Fatal("stopped service left live socket", path, e)
				}
			}
		})
	}
}
func TestUnprivilegedExecutableRejectsEveryVerbDespiteSpoofedSudoIdentity(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("requires ordinary account to exercise EUID boundary")
	}
	binary := filepath.Join(t.TempDir(), "cloudrig-admin")
	build := exec.Command("go", "build", "-o", binary, ".")
	if output, e := build.CombinedOutput(); e != nil {
		t.Fatalf("native build failed: %s %v", output, e)
	}
	for _, verb := range []string{"daemon", "login", "rent", "approve", "start", "destroy", "self-test"} {
		t.Run(verb, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, binary, verb, "vastai", "101")
			cmd.Env = append(os.Environ(), "SUDO_UID=0", "SUDO_USER=root")
			output, e := cmd.CombinedOutput()
			if ctx.Err() != nil || e == nil || !strings.Contains(string(output), "must run as root") {
				t.Fatal("unprivileged executable accepted forged sudo identity", string(output), e)
			}
		})
	}
}
