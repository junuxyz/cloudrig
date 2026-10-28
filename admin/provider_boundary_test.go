package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestMalformedInstanceListsAreNotEmptyAccounts(t *testing.T) {
	for _, provider := range []string{"vastai", "runpod"} {
		for _, payload := range []string{`{}`, `[]`, `{"instances":null,"pods":null}`, `{"instances":{},"pods":{}}`} {
			t.Run(provider+"/"+payload, func(t *testing.T) {
				s := testStore(t)
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					w.Header().Set("Content-Type", "application/json")
					w.Write([]byte(payload))
				}))
				defer server.Close()
				s.Base[provider] = server.URL
				if rows, err := s.instances(provider); err == nil {
					t.Fatalf("malformed response reported a valid account with %d instances", len(rows))
				}
			})
		}
	}
}

func TestCredentialValidationDoesNotReplaceTheLiveToken(t *testing.T) {
	s := testStore(t)
	tokenPath := filepath.Join(s.Root, "vastai.token")
	previous, err := os.ReadFile(tokenPath)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		current, err := os.ReadFile(tokenPath)
		if err != nil || string(current) != string(previous) {
			t.Error("unvalidated token was visible to other provider requests")
		}
		if r.Header.Get("Authorization") != "Bearer NEW_LINKED_SECRET" {
			t.Error("validation did not use the candidate key")
		}
		json.NewEncoder(w).Encode(map[string]any{"instances": []any{}})
	}))
	defer server.Close()
	s.Base["vastai"] = server.URL
	if err := s.linkToken("vastai", "NEW_LINKED_SECRET"); err != nil {
		t.Fatal(err)
	}
}

func TestMalformedListCannotClearAnUnresolvedRental(t *testing.T) {
	s := testStore(t)
	if err := writeJSON(filepath.Join(s.Root, "resources.json"), DB{
		Pending:  map[string]bool{"vastai/training": true},
		Requests: map[string]Record{"vastai/training": {Provider: "vastai", Name: "training", Key: "original-key"}},
	}); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{}`))
	}))
	defer server.Close()
	s.Base["vastai"] = server.URL
	if err := s.recoverRental("vastai", "training", "-"); err == nil {
		t.Fatal("malformed response cleared recovery state")
	}
	db, err := s.readDB()
	if err != nil || !db.Pending["vastai/training"] || db.Requests["vastai/training"].Key != "original-key" {
		t.Fatal("unresolved rental state changed", err)
	}
}

func TestUnsafeEndpointTextIsRejectedBeforePrivilegedOutput(t *testing.T) {
	row := map[string]any{
		"id": "101", "label": "training", "gpu_name": "RTX_5090", "num_gpus": float64(1),
		"dph_total": float64(1), "actual_status": "running", "ssh_host": "host.example", "ssh_port": float64(22),
	}
	if _, err := parseInstance("vastai", row); err != nil {
		t.Fatal("invalid fixture", err)
	}
	row["ssh_host"] = "host\nINJECTED"
	if _, err := parseInstance("vastai", row); err == nil {
		t.Fatal("accepted terminal control characters in SSH metadata")
	}
}

func TestFailedCredentialCommitKeepsTheOriginalToken(t *testing.T) {
	s := testStore(t)
	tokenPath := filepath.Join(s.Root, "vastai.token")
	previous, err := os.ReadFile(tokenPath)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate a storage failure after validation starts, before approvals can be committed.
		if err := os.Mkdir(filepath.Join(s.Root, "resources.json"), 0700); err != nil {
			t.Error(err)
		}
		json.NewEncoder(w).Encode(map[string]any{"instances": []any{}})
	}))
	defer server.Close()
	s.Base["vastai"] = server.URL
	if err := s.linkToken("vastai", "NEW_LINKED_SECRET"); err == nil {
		t.Fatal("credential change succeeded despite a journal write failure")
	}
	token, err := os.ReadFile(tokenPath)
	if err != nil || string(token) != string(previous) {
		t.Fatal("failed commit replaced the previous credential", err)
	}
}

func TestMalformedPaginationCannotHideAccountResources(t *testing.T) {
	for _, sample := range []struct{ provider, body string }{
		{"vastai", `{"instances":[],"next_token":123}`},
		{"runpod", `{"pods":[],"pagination":[]}`},
		{"runpod", `{"pods":[],"pagination":{"hasNextPage":true}}`},
		{"runpod", `{"pods":[],"pagination":{"nextCursor":123}}`},
	} {
		t.Run(sample.provider+sample.body, func(t *testing.T) {
			s := testStore(t)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Write([]byte(sample.body))
			}))
			defer server.Close()
			s.Base[sample.provider] = server.URL
			if _, err := s.instances(sample.provider); err == nil {
				t.Fatal("invalid pagination returned a successful account listing")
			}
		})
	}
}
