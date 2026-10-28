package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	syncatomic "sync/atomic"
	"testing"
	"time"
)

func startTestAPI(t *testing.T, s *Store) string {
	t.Helper()
	path := filepath.Join(s.Run, "api.sock")
	l, e := socketListener(path, os.Getuid(), os.Getgid())
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { l.Close() })
	go s.serveAPI(l)
	return path
}
func callTestAPI(t *testing.T, path, body string) map[string]any {
	t.Helper()
	reply, e := exchangeTestAPI(path, body)
	if e != nil {
		t.Fatal(e)
	}
	return reply
}
func exchangeTestAPI(path, body string) (map[string]any, error) {
	c, e := net.DialTimeout("unix", path, time.Second)
	if e != nil {
		return nil, e
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(2 * time.Second))
	// Oversized input can cause the server to close before the complete write finishes.
	io.WriteString(c, body+"\n")
	c.(*net.UnixConn).CloseWrite()
	var reply map[string]any
	e = json.NewDecoder(c).Decode(&reply)
	return reply, e
}
func TestUnixAPIEnforcesAuthorityAtTheWire(t *testing.T) {
	s := testStore(t)
	var stops syncatomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer ROOT_SECRET_MARKER" {
			t.Error("missing protected credential at provider")
		}
		switch {
		case r.Method == "GET" && strings.HasPrefix(r.URL.Path, "/v2/pods"):
			json.NewEncoder(w).Encode(map[string]any{
				"pods": []any{
					map[string]any{
						"id":     "pod1",
						"name":   "training",
						"gpu":    map[string]any{"id": "5090", "count": 2},
						"cost":   0.8,
						"status": "RUNNING",
						"env":    map[string]string{"KEY": "PROVIDER_SECRET"},
					},
				},
			})
		case r.Method == "POST" && r.URL.Path == "/v2/pods/pod1/action":
			var body map[string]string
			if e := json.NewDecoder(r.Body).Decode(&body); e != nil || body["action"] != "stop" {
				t.Error("ordinary channel sent a different mutation", body, e)
			}
			stops.Add(1)
			json.NewEncoder(w).Encode(map[string]bool{"success": true})
		default:
			t.Errorf("unexpected provider mutation: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(500)
		}
	}))
	defer server.Close()
	s.Base["runpod"] = server.URL
	if e := writeJSON(filepath.Join(s.Root, "resources.json"), DB{
		Records: map[string]Record{"runpod-pod1": {Provider: "runpod", ID: "pod1", Key: "private-path-not-for-clients"}},
	}); e != nil {
		t.Fatal(e)
	}
	path := startTestAPI(t, s)
	reply := callTestAPI(t, path, "{\"op\":\"instances\",\"provider\":\"runpod\"}")
	rows, ok := reply["data"].([]any)
	if !ok || len(rows) != 1 || object(rows[0])["name"] != "training" ||
		object(rows[0])["count"] != float64(2) {
		t.Fatal("ordinary reads broken", reply)
	}
	raw, _ := json.Marshal(reply)
	for _, secret := range []string{"PROVIDER_SECRET", "ROOT_SECRET_MARKER", "private-path-not-for-clients"} {
		if strings.Contains(string(raw), secret) {
			t.Fatal("wire credential leak", secret)
		}
	}
	for _, op := range []string{
		"rent",
		"start",
		"restart",
		"destroy",
		"approve",
		"recover",
		"login",
		"resize",
		"payment",
		"exec",
		"batch",
		"approve_all",
		"register_key",
		"read_file",
		"sign",
	} {
		body, _ := json.Marshal(map[string]any{"op": op, "provider": "runpod", "id": "pod1"})
		if result := callTestAPI(t, path, string(body)); result["error"] == nil {
			t.Errorf("ordinary wire operation %s was accepted: %#v", op, result)
		}
	}
	for _, bad := range []string{
		"{",
		"[]",
		"null",
		"{\"op\":\"instances\",\"provider\":\"runpod\",\"api_url\":\"https://evil\"}",
		"{\"op\":\"stop\",\"provider\":\"runpod\",\"id\":\"" + strings.Repeat("X", 17000) + "\"}",
	} {
		result, e := exchangeTestAPI(path, bad)
		if timeout, ok := e.(net.Error); ok && timeout.Timeout() {
			t.Fatal("malformed request hung instead of being refused", e)
		}
		if e == nil && result["error"] == nil {
			t.Error("malformed/oversized request accepted", result)
		}
	}
	if stops.Load() != 0 {
		t.Fatal("denied request mutated provider")
	}
	if result := callTestAPI(t, path, "{\"op\":\"stop\",\"provider\":\"runpod\",\"id\":\"pod1\"}"); result["error"] != nil || stops.Load() != 1 {
		t.Fatal("approved stop failed", result)
	}
}
func TestUnixAPICannotImpersonateConfiguredOwner(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("root is deliberately allowed; run as an ordinary UID")
	}
	s := testStore(t)
	s.Owner.UID = os.Getuid() + 1000
	path := startTestAPI(t, s)
	c, e := net.DialTimeout("unix", path, time.Second)
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(2 * time.Second))
	// Claiming a UID in JSON must not change the identity the kernel supplies.
	fmt.Fprintf(c, "{\"op\":\"instances\",\"provider\":\"runpod\",\"uid\":%d}\n", s.Owner.UID)
	b := make([]byte, 1)
	n, e := c.Read(b)
	if timeout, ok := e.(net.Error); ok && timeout.Timeout() {
		t.Fatal("unauthorized connection hung instead of closing")
	}
	if n != 0 || e == nil {
		t.Fatal("unauthorized peer received data", b[:n], e)
	}
}
func TestProviderPaginationReturnsAllRowsAndRejectsCycles(t *testing.T) {
	for _, cycle := range []bool{false, true} {
		t.Run(fmt.Sprint(cycle), func(t *testing.T) {
			s := testStore(t)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				cursor := r.URL.Query().Get("cursor")
				if cursor != "" && cursor != "page/+?token" {
					t.Errorf("cursor corrupted: %q", cursor)
				}
				id, next, cost := "first", "page/+?token", 0.5
				if cursor != "" {
					id, cost = "second", 0.7
					if !cycle {
						next = ""
					}
				}
				json.NewEncoder(w).Encode(map[string]any{
					"pods":       []any{map[string]any{"id": id, "name": id, "cost": cost}},
					"pagination": map[string]string{"nextCursor": next},
				})
			}))
			defer server.Close()
			s.Base["runpod"] = server.URL
			rows, e := s.instances("runpod")
			if cycle {
				if e == nil || rows != nil {
					t.Fatal("cursor cycle returned partial success", rows, e)
				}
				return
			}
			if e != nil || len(rows) != 2 || rows[0].ID != "first" || rows[1].ID != "second" ||
				rows[0].Hourly != 0.5 ||
				rows[1].Hourly != 0.7 {
				t.Fatal("incomplete provider pagination", rows, e)
			}
		})
	}
}
func TestRunPodStartRequiresVerifiableGPUCountAndExactCloudPrice(t *testing.T) {
	for _, count := range []any{2, nil, true, 1.5, 0, -1} {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			s := testStore(t)
			var starts syncatomic.Int64
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/v2/pods/pod1":
					json.NewEncoder(w).Encode(map[string]any{
						"id":    "pod1",
						"name":  "training",
						"cloud": "SECURE",
						"gpu":   map[string]any{"id": "5090", "count": count},
						"cost":  1.8,
					})
				case "/v2/catalog/gpus":
					json.NewEncoder(w).Encode(map[string]any{
						"gpus": []any{
							map[string]any{"id": "other", "price": map[string]float64{"secure": 0.01}},
							map[string]any{"id": "5090", "price": map[string]float64{"secure": 0.9, "community": 0.3}},
						},
					})
				case "/v2/pods/pod1/action":
					var body map[string]string
					json.NewDecoder(r.Body).Decode(&body)
					if r.Method != "POST" || body["action"] != "start" {
						t.Error("wrong start operation", body)
					}
					starts.Add(1)
					json.NewEncoder(w).Encode(map[string]bool{"success": true})
				default:
					t.Error("unexpected provider request", r.URL.Path)
					w.WriteHeader(500)
				}
			}))
			defer server.Close()
			s.Base["runpod"] = server.URL
			var quote bytes.Buffer
			s.Output = &quote
			e := s.mutate("runpod", "pod1", "start")
			if count == 2 {
				if e != nil || starts.Load() != 1 || !strings.Contains(quote.String(), "$1.800000/hour") {
					t.Fatal("wrong GPU/cloud/count quote", quote, starts.Load(), e)
				}
			} else if e == nil || starts.Load() != 0 {
				t.Fatal("unverifiable GPU count permitted billable start", count, quote, e)
			}
		})
	}
}

type bytewiseReader struct{ data []byte }

func (r *bytewiseReader) Read(b []byte) (int, error) {
	if len(r.data) == 0 {
		return 0, io.EOF
	}
	b[0] = r.data[0]
	r.data = r.data[1:]
	return 1, nil
}
func TestAgentFramesSurviveFragmentationAndRejectTruncation(t *testing.T) {
	wire := []byte{0, 0, 0, 4, 11, 1, 2, 3}
	got, e := frame(&bytewiseReader{data: wire})
	if e != nil || !reflect.DeepEqual(got, wire) {
		t.Fatal("fragmented frame lost bytes", got, e)
	}
	for size := 0; size < len(wire); size++ {
		if _, e := frame(&bytewiseReader{data: wire[:size]}); e == nil {
			t.Fatalf("truncated frame of %d bytes accepted", size)
		}
	}
}
func FuzzAgentFrames(f *testing.F) {
	for _, seed := range [][]byte{{0, 0, 0, 1, 11}, {0, 0, 0, 0}, {0xff, 0xff, 0xff, 0xff}, {0, 0, 0, 1, 18}} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, input []byte) {
		b, e := frame(strings.NewReader(string(input)))
		if e != nil {
			return
		}
		if len(input) < 4 {
			t.Fatal("accepted missing length")
		}
		size := int(binary.BigEndian.Uint32(input[:4]))
		if size < 1 || size > 262144 || len(b) != size+4 || len(input) < size+4 ||
			string(b) != string(input[:size+4]) {
			t.Fatal("accepted corrupt frame")
		}
		if permittedFrame(b) && b[4] != 11 && b[4] != 13 && b[4] != 27 {
			t.Fatal("unapproved agent message permitted")
		}
	})
}
func FuzzProviderInstance(f *testing.F) {
	f.Add("runpod", []byte("{\"id\":\"pod1\",\"name\":\"training\",\"cost\":0.5}"))
	f.Add("vastai", []byte("{\"id\":1,\"num_gpus\":2,\"dph_total\":0.5}"))
	f.Fuzz(func(t *testing.T, provider string, data []byte) {
		if provider != "runpod" && provider != "vastai" {
			return
		}
		var row map[string]any
		if json.Unmarshal(data, &row) != nil {
			return
		}
		i, e := parseInstance(provider, row)
		if e != nil {
			return
		}
		if len(i.ID) < 1 || len(i.ID) > 100 || i.Count < 1 || i.Hourly < 0 || math.IsNaN(i.Hourly) ||
			math.IsInf(i.Hourly, 0) {
			t.Fatal("accepted unsafe instance")
		}
		for _, c := range i.ID {
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-') {
				t.Fatal("unsafe instance ID")
			}
		}
	})
}
