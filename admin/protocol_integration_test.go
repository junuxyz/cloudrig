package main

import (
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
	path := startTestAPI(t, s)
	for _, operation := range []string{"stop", "start", "destroy", "rent", "approve"} {
		reply := callTestAPI(t, path, `{"op":"`+operation+`","provider":"vastai","id":"1"}`)
		if reply["error"] == nil {
			t.Fatalf("unprivileged API accepted %s", operation)
		}
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
