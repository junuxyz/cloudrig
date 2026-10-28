package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	syncatomic "sync/atomic"
	"testing"
	"time"
)

type writerFunc func([]byte) (int, error)

func (f writerFunc) Write(data []byte) (int, error) { return f(data) }

type vastProvider struct {
	server         *httptest.Server
	allocations    syncatomic.Int64
	deletions      syncatomic.Int64
	starts         syncatomic.Int64
	price          syncatomic.Uint64
	listed         syncatomic.Bool
	failAllocation syncatomic.Bool
	failDelete     syncatomic.Bool
	failStart      syncatomic.Bool
	lostResponse   syncatomic.Int64
	endpointPort   syncatomic.Int64
}

func newVastProvider(t *testing.T, s *Store) *vastProvider {
	t.Helper()
	f := &vastProvider{}
	f.price.Store(math.Float64bits(0.8))
	f.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer ROOT_SECRET_MARKER" {
			t.Error("provider request did not use the protected fixture credential")
		}
		write := func(v any) {
			if e := json.NewEncoder(w).Encode(v); e != nil {
				t.Error(e)
			}
		}
		switch {
		case r.URL.Path == "/api/v0/gpu_names/unique/":
			write(map[string]any{"gpu_names": []string{"RTX 5090"}})
		case r.URL.Path == "/api/v0/bundles/":
			write(map[string]any{
				"offers": []any{
					map[string]any{
						"id":           1,
						"gpu_name":     "RTX 5090",
						"num_gpus":     2,
						"gpu_ram":      32768,
						"dph_base":     math.Float64frombits(f.price.Load()),
						"storage_cost": 0.72,
						"disk_space":   100,
						"geolocation":  "US",
					},
				},
			})
		case r.URL.Path == "/api/v1/instances/":
			rows := []any{}
			if f.listed.Load() || f.endpointPort.Load() > 0 && f.allocations.Load() > 0 {
				rows = append(rows, map[string]any{
					"id":            101,
					"label":         "training",
					"gpu_name":      "RTX 5090",
					"num_gpus":      2,
					"dph_base":      math.Float64frombits(f.price.Load()),
					"dph_total":     0.84,
					"actual_status": "running",
					"ssh_host":      "127.0.0.1",
					"ssh_port":      f.endpointPort.Load(),
				})
			}
			write(map[string]any{"instances": rows})
		case r.Method == "PUT" && r.URL.Path == "/api/v0/asks/1/":
			var body map[string]any
			if e := json.NewDecoder(r.Body).Decode(&body); e != nil {
				t.Error(e)
			}
			if body["disk"] != float64(40) || body["image"] != "pytorch/pytorch:tag" || body["client_id"] != "me" ||
				body["runtype"] != "ssh_direct" {
				t.Errorf("invalid allocation payload: %#v", body)
			}
			db, e := s.readDB()
			if e != nil || !db.Pending["vastai/"+text(body["label"])] {
				t.Error("allocation reached provider without durable pending receipt", e)
			}
			id := 100 + f.allocations.Add(1)
			switch f.lostResponse.Load() {
			case 1:
				connection, _, e := w.(http.Hijacker).Hijack()
				if e != nil {
					t.Error(e)
					return
				}
				connection.Close()
				return
			case 2:
				select {
				case <-r.Context().Done():
				case <-time.After(2 * time.Second):
					t.Error("timed-out request did not cancel its provider connection")
				}
				return
			}
			if f.failAllocation.Load() {
				w.WriteHeader(503)
				write(map[string]any{"error": "PRIVATE_PROVIDER_DETAIL"})
				return
			}
			write(map[string]any{"new_contract": id})
		case r.Method == "POST" && strings.HasSuffix(r.URL.Path, "/ssh/"):
			db, e := s.readDB()
			if e != nil || len(db.Records) == 0 {
				t.Error("key registration reached provider before allocation ID was recorded", e)
			}
			var body map[string]string
			if e := json.NewDecoder(r.Body).Decode(&body); e != nil || !strings.HasPrefix(body["ssh_key"], "ssh-ed25519 ") ||
				strings.Contains(body["ssh_key"], "PRIVATE") {
				t.Error("not a public-key-only registration", body, e)
			}
			if f.endpointPort.Load() > 0 {
				write(map[string]bool{"success": true})
			} else {
				w.WriteHeader(503)
			}
		case r.Method == "DELETE" && r.URL.Path == "/api/v0/instances/101/":
			f.deletions.Add(1)
			if _, e := s.approved("vastai", "101"); e == nil {
				t.Error("delete reached provider before SSH authority was revoked")
			}
			if f.failDelete.Load() {
				w.WriteHeader(503)
				return
			}
			write(map[string]bool{"success": true})
		case r.Method == "PUT" && r.URL.Path == "/api/v0/instances/101/":
			var body map[string]string
			if e := json.NewDecoder(r.Body).Decode(&body); e != nil || body["state"] != "running" {
				t.Error("wrong start payload", body, e)
			}
			f.starts.Add(1)
			if f.failStart.Load() {
				w.WriteHeader(503)
				return
			}
			write(map[string]bool{"success": true})
		default:
			t.Errorf("unexpected provider operation %s %s", r.Method, r.URL.String())
			w.WriteHeader(500)
		}
	}))
	t.Cleanup(f.server.Close)
	s.Base["vastai"] = f.server.URL
	return f
}

func rental(name string) RentArgs {
	return RentArgs{"vastai", "1", "5090", name, "pytorch/pytorch:tag", 40, 1, nil}
}

func TestAmbiguousRentalSurvivesBrokerRestartAndUnrelatedRental(t *testing.T) {
	s := testStore(t)
	f := newVastProvider(t, s)
	f.failAllocation.Store(true)
	id, e := s.rent(rental("training"))
	if e == nil || id != "" || strings.Contains(e.Error(), "PRIVATE_PROVIDER_DETAIL") {
		t.Fatal("unsafe allocation failure", id, e)
	}
	recovered := newStore(s.Root, s.Run, s.Owner)
	recovered.Base["vastai"] = f.server.URL
	if _, e = recovered.rent(rental("training")); e == nil || !strings.Contains(e.Error(), "unknown outcome") {
		t.Fatal("ambiguous allocation retried after restart", e)
	}
	if n := f.allocations.Load(); n != 1 {
		t.Fatalf("duplicate allocation: %d requests", n)
	}
	if _, e = recovered.rent(rental("another")); e == nil {
		t.Fatal("fault fixture unexpectedly allocated successfully")
	}
	db, e := recovered.readDB()
	if e != nil || !db.Pending["vastai/training"] || !db.Pending["vastai/another"] ||
		f.allocations.Load() != 2 {
		t.Fatal("unrelated rental erased recovery state", db, e)
	}
}

func TestKnownAllocationCannotBeDuplicatedWhenProviderListingIsStale(t *testing.T) {
	s := testStore(t)
	f := newVastProvider(t, s)
	id, e := s.rent(rental("training"))
	if id != "101" || e != nil {
		t.Fatal("allocation should succeed while SSH is pending", id, e)
	}
	recovered := newStore(s.Root, s.Run, s.Owner)
	recovered.Base["vastai"] = f.server.URL
	db, e := recovered.readDB()
	rec := db.Records["vastai-101"]
	if e != nil || rec.ID != "101" || rec.Name != "training" || rec.Key == "" {
		t.Fatal("lost allocated resource", db, e)
	}
	key, e := os.ReadFile(rec.Key)
	if e != nil || !strings.Contains(string(key), "PRIVATE KEY") {
		t.Fatal("missing protected fixture private key", e)
	}
	if _, e = recovered.rent(rental("training")); e == nil {
		t.Fatal("same rental was allowed again")
	}
	if n := f.allocations.Load(); n != 1 {
		t.Fatalf("stale provider listing caused %d allocations", n)
	}
}

func TestConcurrentBrokersCannotRentSameNameTwice(t *testing.T) {
	s := testStore(t)
	f := newVastProvider(t, s)
	ready := make(chan struct{}, 2)
	release := make(chan struct{})
	type result struct {
		id  string
		err error
	}
	results := make(chan result, 2)
	for range 2 {
		other := newStore(s.Root, s.Run, s.Owner)
		other.Base["vastai"] = f.server.URL
		other.Output = writerFunc(func(data []byte) (int, error) {
			ready <- struct{}{}
			<-release
			return len(data), nil
		})
		go func() {
			id, e := other.rent(rental("training"))
			results <- result{id, e}
		}()
	}
	for range 2 {
		select {
		case <-ready:
		case <-time.After(5 * time.Second):
			close(release)
			t.Fatal("rentals did not overlap at quote output")
		}
	}
	close(release)
	allocated := 0
	for range 2 {
		select {
		case r := <-results:
			if r.id != "" {
				allocated++
			}
			if (r.id != "") != (r.err == nil) {
				t.Error("only the allocated rental should succeed", r)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("concurrent broker deadlocked")
		}
	}
	db, e := s.readDB()
	if e != nil || allocated != 1 || f.allocations.Load() != 1 || len(db.Records) != 1 {
		t.Fatalf("duplicate spending: results=%d provider=%d records=%d err=%v", allocated, f.allocations.Load(), len(db.Records), e)
	}
}

func TestDeleteRevokesBeforeNetworkAndOnlyConfirmedDeletionReleasesName(t *testing.T) {
	s := testStore(t)
	f := newVastProvider(t, s)
	if id, e := s.rent(rental("training")); id != "101" || e != nil {
		t.Fatal(id, e)
	}
	f.listed.Store(true)
	f.failDelete.Store(true)
	if e := s.mutate("vastai", "101", "destroy"); e == nil {
		t.Fatal("provider deletion failure was hidden")
	}
	db, e := s.readDB()
	if e != nil || !db.Records["vastai-101"].Revoked {
		t.Fatal("failed delete retained signing authority", db, e)
	}
	f.listed.Store(false)
	if _, e = s.rent(rental("training")); e == nil || f.allocations.Load() != 1 {
		t.Fatal("uncertain delete released the rental name", e)
	}
	f.listed.Store(true)
	f.failDelete.Store(false)
	if e = s.mutate("vastai", "101", "destroy"); e != nil {
		t.Fatal(e)
	}
	f.listed.Store(false)
	if id, e := s.rent(rental("training")); id != "102" || e != nil {
		t.Fatal("confirmed deletion did not permit name reuse", id, e)
	}
	if f.deletions.Load() != 2 || f.allocations.Load() != 2 {
		t.Fatal("wrong number of resource mutations")
	}
}

func TestSuccessfulRentalCanAuthenticateUsingOnlyPublishedPublicGrant(t *testing.T) {
	for _, provider := range []string{"vastai", "runpod"} {
		t.Run(provider, func(t *testing.T) {
			s := testStore(t)
			endpoint := newSSHFixture(t, nil)
			args := rental("training")
			expectedID := "101"
			var output bytes.Buffer
			var port syncatomic.Int64
			var vast *vastProvider
			s.Output = &output
			if provider == "vastai" {
				vast = newVastProvider(t, s)
			} else {
				args = RentArgs{"runpod", "gpu5090|SECURE|US-TX", "5090", "training", "pytorch/pytorch:tag", 40, 2, nil}
				expectedID = "pod1"
				var allocations syncatomic.Int64
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Header.Get("Authorization") != "Bearer ROOT_SECRET_MARKER" {
						t.Error("missing protected credential")
					}
					write := func(v any) { json.NewEncoder(w).Encode(v) }
					switch {
					case r.URL.Path == "/v2/catalog/gpus":
						write(map[string]any{
							"gpus": []any{
								map[string]any{
									"id":           "gpu5090",
									"name":         "RTX 5090",
									"memory":       32,
									"secure":       true,
									"community":    false,
									"availability": "HIGH",
									"maxCount":     map[string]int{"secure": 4},
									"price":        map[string]float64{"secure": 0.4},
									"dataCenters":  []any{map[string]string{"id": "US-TX", "availability": "HIGH"}},
								},
							},
						})
					case r.Method == "GET" && r.URL.Path == "/v2/pods":
						write(map[string]any{"pods": []any{}})
					case r.Method == "POST" && r.URL.Path == "/v2/pods":
						var body map[string]any
						json.NewDecoder(r.Body).Decode(&body)
						gpu := object(body["gpu"])
						public := text(object(body["env"])["PUBLIC_KEY"])
						if gpu["id"] != "gpu5090" || gpu["count"] != float64(2) || body["cloud"] != "SECURE" ||
							body["disk"] != float64(40) ||
							body["image"] != "pytorch/pytorch:tag" ||
							!strings.HasPrefix(public, "ssh-ed25519 ") ||
							strings.Contains(public, "PRIVATE") {
							t.Error("wrong RunPod rental contract", body)
						}
						if allocations.Add(1) != 1 {
							t.Error("duplicate RunPod allocation")
						}
						write(map[string]string{"id": "pod1"})
					case r.Method == "GET" && r.URL.Path == "/v2/pods/pod1":
						write(map[string]any{
							"id":     "pod1",
							"name":   "training",
							"status": "running",
							"gpu":    map[string]any{"id": "gpu5090", "count": 2},
							"cost":   0.8,
							"ssh":    map[string]any{"direct": map[string]any{"host": "127.0.0.1", "port": port.Load(), "username": "worker"}},
						})
					default:
						t.Error("unexpected RunPod request", r.Method, r.URL.Path)
						w.WriteHeader(500)
					}
				}))
				t.Cleanup(server.Close)
				s.Base["runpod"] = server.URL
			}
			id, e := s.rent(args)
			if e != nil || id != expectedID {
				t.Fatal("rental did not complete", id, e)
			}
			expectedQuote := "$0.840000/hour"
			if provider == "runpod" {
				expectedQuote = "$0.800000/hour"
			}
			if !strings.Contains(output.String(), expectedQuote) || !strings.Contains(output.String(), "x2") {
				t.Fatal("wrong provider-derived rental quote", output.String())
			}
			if vast != nil {
				vast.listed.Store(true)
			}
			pending, e := s.public(Request{Op: "instance", Provider: provider, ID: id})
			if e != nil || !pending.(Instance).Approved || pending.(Instance).AgentSocket != "" {
				t.Fatal("booting rental should retain its approval without publishing a signer", pending, e)
			}
			if vast != nil {
				vast.endpointPort.Store(int64(endpoint.port))
			} else {
				port.Store(int64(endpoint.port))
			}
			recovered := newStore(s.Root, s.Run, s.Owner)
			recovered.Base = s.Base
			if _, e = recovered.public(Request{Op: "instance", Provider: provider, ID: id}); e != nil {
				t.Fatal("delayed connection did not recover after helper restart", e)
			}
			rec, e := recovered.approved(provider, id)
			if e != nil || rec.Connecting {
				t.Fatal("successful rental lost its connection authority", rec, e)
			}
			stop, e := s.startSigner(rec)
			if e != nil {
				t.Fatal(e)
			}
			t.Cleanup(stop)
			i, e := s.instance(provider, id)
			if e != nil {
				t.Fatal(e)
			}
			grant := s.publicGrant(i)
			encoded, e := json.Marshal(grant)
			if e != nil {
				t.Fatal(e)
			}
			if !grant.Approved || strings.Contains(string(encoded), rec.Key) ||
				strings.Contains(string(encoded), "PRIVATE KEY") {
				t.Fatal("unsafe or missing public grant", string(encoded))
			}
			info, e := os.Stat(rec.Key)
			if e != nil || info.Mode().Perm() != 0600 {
				t.Fatal("private key permissions", info, e)
			}
			out, e := sshAuthentication(grant.PublicKey, grant.AgentSocket, grant.KnownHosts, provider+"-"+id, grant.User, grant.Port)
			if e != nil || out != "authenticated\n" {
				t.Fatal("rental grant cannot authenticate", out, e)
			}
		})
	}
}

func TestInstanceReadCannotAuthorizeOrRepinAnUnrequestedConnection(t *testing.T) {
	for _, state := range []string{"unknown", "revoked", "completed"} {
		t.Run(state, func(t *testing.T) {
			s, rec, _, other := signerRecord(t)
			rec.Revoked = state == "revoked"
			rec.Connecting = state == "revoked"
			db := DB{Records: map[string]Record{keyOf(rec.Provider, rec.ID): rec}}
			if state == "unknown" {
				delete(db.Records, keyOf(rec.Provider, rec.ID))
			}
			journal := filepath.Join(s.Root, "resources.json")
			if e := writeJSON(journal, db); e != nil {
				t.Fatal(e)
			}
			before, _ := os.ReadFile(journal)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "GET" || r.URL.Path != "/api/v1/instances/" {
					t.Error("read attempted to register an unrequested key", r.Method, r.URL.Path)
					w.WriteHeader(500)
					return
				}
				json.NewEncoder(w).Encode(map[string]any{"instances": []any{map[string]any{
					"id": rec.ID, "label": "fixture", "actual_status": "running", "ssh_host": "127.0.0.1", "ssh_port": other.port,
				}}})
			}))
			t.Cleanup(server.Close)
			s.Base["vastai"] = server.URL
			result, e := s.public(Request{Op: "instance", Provider: rec.Provider, ID: rec.ID})
			if e != nil {
				t.Fatal(e)
			}
			grant := result.(Instance)
			if state == "completed" {
				if !grant.Approved || grant.Port != rec.Port {
					t.Fatal("read replaced the approved endpoint", grant)
				}
			} else if grant.Approved {
				t.Fatal("read granted unapproved SSH access", grant)
			}
			after, _ := os.ReadFile(journal)
			if !bytes.Equal(before, after) {
				t.Fatal("read changed a record without connection authority")
			}
		})
	}
}

func TestUntrustedProviderPricesCannotBecomeFreeRentalQuotes(t *testing.T) {
	for _, price := range []any{0.4, "NaN", "Infinity", "-1", true, map[string]any{"unexpected": "shape"}} {
		t.Run(fmt.Sprint(price), func(t *testing.T) {
			s := testStore(t)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				json.NewEncoder(w).Encode(map[string]any{
					"gpus": []any{
						map[string]any{
							"id":           "gpu5090",
							"name":         "RTX 5090",
							"memory":       32,
							"secure":       true,
							"community":    false,
							"availability": "HIGH",
							"maxCount":     map[string]int{"secure": 4},
							"price":        map[string]any{"secure": price},
							"dataCenters":  []any{map[string]string{"id": "US-TX", "availability": "HIGH"}},
						},
					},
				})
			}))
			defer server.Close()
			s.Base["runpod"] = server.URL
			rows, e := s.offers(Request{Provider: "runpod", Query: "5090", Disk: 40, Count: 2})
			if price == 0.4 {
				if e != nil || len(rows) != 1 || rows[0].Hourly != 0.8 || rows[0].Count != 2 {
					t.Fatal("valid quote failed", rows, e)
				}
			} else if e == nil || rows != nil {
				t.Fatal("invalid price produced usable offers", rows, e)
			}
		})
	}
}

func TestLostAllocationResponseNeverTriggersAutomaticOrRestartRetry(t *testing.T) {
	for _, fault := range []int64{1, 2} {
		t.Run(fmt.Sprint(fault), func(t *testing.T) {
			s := testStore(t)
			f := newVastProvider(t, s)
			f.lostResponse.Store(fault)
			s.Client.Timeout = 250 * time.Millisecond
			id, e := s.rent(rental("training"))
			if e == nil || id != "" || f.allocations.Load() != 1 {
				t.Fatal("lost response retried or reported success", id, f.allocations.Load(), e)
			}
			recovered := newStore(s.Root, s.Run, s.Owner)
			recovered.Base["vastai"] = f.server.URL
			if _, e = recovered.rent(rental("training")); e == nil || f.allocations.Load() != 1 {
				t.Fatal("restarted broker duplicated unknown allocation", f.allocations.Load(), e)
			}
			db, e := recovered.readDB()
			if e != nil || !db.Pending["vastai/training"] {
				t.Fatal("lost response had no durable recovery record", db, e)
			}
		})
	}
}
