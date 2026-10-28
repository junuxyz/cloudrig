package main

import (
	"fmt"
	"net"
	"path/filepath"
	"testing"
)

func TestInstallationChecksTheRunningServiceProtocol(t *testing.T) {
	for _, reply := range []string{`{"data":{"protocol":4}}`, `{"data":{"protocol":2}}`, `{"error":"unavailable"}`, `broken`} {
		t.Run(reply, func(t *testing.T) {
			s := testStore(t)
			listener, err := net.Listen("unix", filepath.Join(s.Run, "api.sock"))
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			done := make(chan struct{})
			go func() {
				defer close(done)
				connection, err := listener.Accept()
				if err != nil {
					return
				}
				defer connection.Close()
				request := make([]byte, 4096)
				connection.Read(request)
				fmt.Fprintln(connection, reply)
			}()
			err = s.checkService()
			if (err == nil) != (reply == `{"data":{"protocol":4}}`) {
				t.Fatal("wrong readiness result", err)
			}
			<-done
		})
	}
	s := testStore(t)
	if err := s.checkService(); err == nil {
		t.Fatal("missing service accepted")
	}
}
