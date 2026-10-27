package main

import (
	"bytes"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

type approvalTerminal struct {
	prompt bytes.Buffer
	input  io.Reader
	answer func(string) string
}

func (f *approvalTerminal) Write(b []byte) (int, error) { return f.prompt.Write(b) }
func (f *approvalTerminal) Read(b []byte) (int, error) {
	if f.input == nil {
		f.input = strings.NewReader(f.answer(f.prompt.String()))
	}
	return f.input.Read(b)
}

func TestEachOperationRequiresItsOwnApproval(t *testing.T) {
	var previous string
	first := &approvalTerminal{answer: func(prompt string) string {
		previous = regexp.MustCompile(`APPROVE [0-9a-f]{12}`).FindString(prompt)
		return previous + "\n"
	}}
	if e := requestApproval(first, "RENT vastai/1 at $0.50/hour"); e != nil {
		t.Fatal(e)
	}
	second := &approvalTerminal{answer: func(string) string { return previous + "\n" }}
	if e := requestApproval(second, "DESTROY vastai/2"); e == nil {
		t.Fatal("approval for the previous operation was reused")
	}
}

func TestBlankGenericBatchAndOversizedApprovalAreDenied(t *testing.T) {
	for _, input := range []string{"\n", "yes\n", "APPROVE\n", "APPROVE ALL\n", "APPROVE\nAPPROVE\n", strings.Repeat("A", 1000) + "\n"} {
		terminal := &approvalTerminal{answer: func(string) string { return input }}
		if e := requestApproval(terminal, "START vastai/1"); e == nil {
			t.Fatalf("accepted generic/bulk input %q", input)
		}
	}
}

func TestCredentialChangeCancellationDoesNotReadOrReplaceKey(t *testing.T) {
	s := testStore(t)
	declined := errors.New("human declined this credential change")
	var request string
	s.Confirm = func(message string) error { request = message; return declined }
	if e := s.login("vastai"); !errors.Is(e, declined) {
		t.Fatal("credential input began before approval", e)
	}
	if !strings.Contains(request, "vastai") || !strings.Contains(request, "revoked") {
		t.Fatal("approval omitted credential-change consequences")
	}
	key, e := os.ReadFile(filepath.Join(s.Root, "vastai.token"))
	if e != nil || string(key) != "ROOT_SECRET_MARKER" {
		t.Fatal("credentials changed after cancellation", e)
	}
}
