package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCredentialRotationCommitsOnlyAfterProviderValidation(t *testing.T) {
	for _, valid := range []bool{false, true} {
		name := "rejected"
		if valid {
			name = "accepted"
		}
		t.Run(name, func(t *testing.T) {
			s := testStore(t)
			initial := DB{Records: map[string]Record{
				"vastai-101":  {Provider: "vastai", ID: "101", Key: "protected-vast-key"},
				"runpod-pod1": {Provider: "runpod", ID: "pod1", Key: "protected-runpod-key"},
			}, Pending: map[string]bool{"vastai/unresolved": true}}
			dbPath := filepath.Join(s.Root, "resources.json")
			if e := writeJSON(dbPath, initial); e != nil {
				t.Fatal(e)
			}
			previousDB, e := os.ReadFile(dbPath)
			if e != nil {
				t.Fatal(e)
			}
			tokenPath := filepath.Join(s.Root, "vastai.token")
			previousToken, e := os.ReadFile(tokenPath)
			if e != nil {
				t.Fatal(e)
			}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("Authorization") != "Bearer NEW_LINKED_SECRET" || r.Method != "GET" ||
					r.URL.Path != "/api/v1/instances/" {
					t.Error("validation used wrong credential or mutated provider", r.Method, r.URL.Path)
				}
				if !valid {
					w.WriteHeader(401)
					w.Write([]byte("SECRET_REJECT_DETAIL"))
					return
				}
				json.NewEncoder(w).Encode(map[string]any{"instances": []any{}})
			}))
			defer server.Close()
			s.Base["vastai"] = server.URL
			e = s.linkToken("vastai", "NEW_LINKED_SECRET")
			token, readErr := os.ReadFile(tokenPath)
			if readErr != nil {
				t.Fatal(readErr)
			}
			afterDB, readErr := os.ReadFile(dbPath)
			if readErr != nil {
				t.Fatal(readErr)
			}
			if !valid {
				if e == nil || !bytes.Equal(token, previousToken) || !bytes.Equal(afterDB, previousDB) {
					t.Fatal("rejected credential change was not rolled back", e)
				}
				return
			}
			if e != nil || string(token) != "NEW_LINKED_SECRET" {
				t.Fatal("validated key did not commit", e)
			}
			db, readErr := s.readDB()
			if readErr != nil || !db.Records["vastai-101"].Revoked || db.Records["runpod-pod1"].Revoked ||
				!db.Pending["vastai/unresolved"] {
				t.Fatal("rotation damaged unrelated authority/recovery state", db, readErr)
			}
			info, e := os.Stat(tokenPath)
			if e != nil || info.Mode().Perm() != 0600 {
				t.Fatal("token lost private permissions", info, e)
			}
		})
	}
}

func TestCredentialInputRejectsHeaderInjectionAndOversizedKeys(t *testing.T) {
	s := testStore(t)
	for _, token := range []string{
		"",
		strings.Repeat("A", 8193),
		"KEY\r\nAuthorization: injected",
		"KEY\x00VALUE",
		"KEY\tVALUE",
		"비밀키",
	} {
		if e := s.linkToken("vastai", token); e == nil {
			t.Fatal("unsafe credential accepted")
		}
	}
	stored, e := os.ReadFile(filepath.Join(s.Root, "vastai.token"))
	if e != nil || string(stored) != "ROOT_SECRET_MARKER" {
		t.Fatal("invalid input damaged existing credential", e)
	}
	maximum := strings.Repeat("A", 8192)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+maximum {
			t.Error("valid maximum-length credential not used exactly")
		}
		json.NewEncoder(w).Encode(map[string]any{"instances": []any{}})
	}))
	defer server.Close()
	s.Base["vastai"] = server.URL
	if e = s.linkToken("vastai", maximum); e != nil {
		t.Fatal("valid maximum-length key rejected", e)
	}
	stored, e = os.ReadFile(filepath.Join(s.Root, "vastai.token"))
	if e != nil || string(stored) != maximum {
		t.Fatal("validated maximum-length key did not commit", e)
	}
}
