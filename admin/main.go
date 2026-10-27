// cloudrig-admin is the only privileged component. It has no dependency on the Python CLI.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"syscall"
)

const stateRoot = "/var/db/cloudrig"

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
	runRoot := "/var/run/cloudrig"
	if runtime.GOOS == "darwin" {
		runRoot = "/Library/PrivilegedHelperTools/cloudrig"
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
