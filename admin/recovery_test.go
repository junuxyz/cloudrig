package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestInvalidRunPodOfferFailsBeforeProviderAccessOrPendingWrites(t *testing.T) {
	s := testStore(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("invalid rental reached the provider")
		w.WriteHeader(500)
	}))
	defer server.Close()
	s.Base["runpod"] = server.URL
	args := rental("training")
	args.Provider, args.ID = "runpod", "invalid"
	if _, e := s.rent(args); e == nil {
		t.Fatal("accepted a malformed RunPod offer")
	}
	if _, e := os.Stat(filepath.Join(s.Root, "resources.json")); !os.IsNotExist(e) {
		t.Fatal("invalid offer created a pending record", e)
	}
}

func TestRecoverRunPodUsesThePendingPerPodKey(t *testing.T) {
	s := testStore(t)
	key, _, e := s.newKey("pending-runpod")
	if e != nil {
		t.Fatal(e)
	}
	db := DB{Pending: map[string]bool{"runpod/training": true}, Requests: map[string]Record{
		"runpod/training": {Provider: "runpod", Name: "training", Key: key},
	}}
	if e = writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
		t.Fatal(e)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" || r.URL.Path != "/v2/pods" {
			t.Errorf("recovery attempted a mutation: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(500)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"pods": []any{map[string]any{
			"id": "pod1", "name": "training", "status": "running", "cost": 0.4,
		}}})
	}))
	defer server.Close()
	s.Base["runpod"] = server.URL
	if e = s.recoverRental("runpod", "training", "pod1"); e != nil {
		t.Fatal(e)
	}
	db, e = s.readDB()
	if e != nil || db.Records["runpod-pod1"].Key != key || !db.Records["runpod-pod1"].Connecting {
		t.Fatal("lost recovered RunPod key", db, e)
	}
}

func TestExplicitClearReleasesAnExternallyDeletedAllocation(t *testing.T) {
	s := testStore(t)
	f := newVastProvider(t, s)
	if _, e := s.rent(rental("training")); e != nil {
		t.Fatal(e)
	}
	f.listed.Store(true)
	if e := s.recoverRental("vastai", "training", "-"); e == nil {
		t.Fatal("cleared a listed instance")
	}
	f.listed.Store(false)
	if e := s.recoverRental("vastai", "training", "-"); e != nil {
		t.Fatal(e)
	}
	db, _ := s.readDB()
	if len(db.Records) != 0 {
		t.Fatal("name still reserved", db)
	}
	if f.allocations.Load() != 1 || f.deletions.Load() != 0 {
		t.Fatal("recovery changed provider resources")
	}
}

func TestRecoverLostRentalKeepsItsOriginalKeyAndDoesNotAllocate(t *testing.T) {
	s := testStore(t)
	f := newVastProvider(t, s)
	f.failAllocation.Store(true)
	if _, e := s.rent(rental("training")); e == nil {
		t.Fatal("expected lost response")
	}
	before, _ := s.readDB()
	original := before.Requests["vastai/training"].Key
	if original == "" {
		t.Fatal("pending receipt lost the key association")
	}
	f.listed.Store(true)
	if e := s.recoverRental("vastai", "training", "-"); e == nil {
		t.Fatal("cleared a listed allocation")
	}
	if e := s.recoverRental("vastai", "training", "999"); e == nil {
		t.Fatal("recovered an unrelated ID")
	}
	if e := s.recoverRental("vastai", "training", "101"); e != nil {
		t.Fatal(e)
	}
	after, _ := s.readDB()
	rec := after.Records["vastai-101"]
	if rec.Key != original || !rec.Connecting || after.Pending["vastai/training"] ||
		len(after.Requests) != 0 {
		t.Fatal("invalid recovered record", after)
	}
	if f.allocations.Load() != 1 {
		t.Fatal("recovery allocated another instance")
	}
}

func TestClearPendingRentalRequiresSuccessfulProviderRead(t *testing.T) {
	s := testStore(t)
	f := newVastProvider(t, s)
	f.failAllocation.Store(true)
	s.rent(rental("training"))
	base := s.Base["vastai"]
	s.Base["vastai"] = "http://127.0.0.1:1"
	if e := s.recoverRental("vastai", "training", "-"); e == nil {
		t.Fatal("cleared without a provider read")
	}
	db, _ := s.readDB()
	if !db.Pending["vastai/training"] {
		t.Fatal("erased pending record")
	}
	s.Base["vastai"] = base
	if e := s.recoverRental("vastai", "training", "-"); e != nil {
		t.Fatal(e)
	}
	db, _ = s.readDB()
	if db.Pending["vastai/training"] {
		t.Fatal("pending name still reserved")
	}
}

func TestOldPendingReceiptsRemainReadable(t *testing.T) {
	s := testStore(t)
	if e := atomic(filepath.Join(s.Root, "resources.json"), []byte(`{"records":{},"pending":{"vastai/training":true}}`), 0600); e != nil {
		t.Fatal(e)
	}
	db, e := s.readDB()
	if e != nil || !db.Pending["vastai/training"] || db.Requests == nil {
		t.Fatal(db, e)
	}
	result, e := s.public(Request{Op: "rental-status", Provider: "vastai", Query: "training"})
	if e != nil || result.(map[string]string)["state"] != "pending" {
		t.Fatal(result, e)
	}
}

func TestRentalCannotExceedThePriceReviewedInTheClient(t *testing.T) {
	s := testStore(t)
	f := newVastProvider(t, s)
	args := rental("training")
	ceiling := 0.5
	args.MaxHourly = &ceiling
	if _, e := s.rent(args); e == nil {
		t.Fatal("rented above reviewed price")
	}
	if f.allocations.Load() != 0 {
		t.Fatal("paid request reached provider")
	}
	result, e := s.public(Request{Op: "rental-status", Provider: "vastai", Query: "training"})
	if e != nil || result.(map[string]string)["state"] != "absent" {
		t.Fatal(result, e)
	}
	ceiling = 0.84
	if _, e := s.rent(args); e != nil {
		t.Fatal(e)
	}
	if f.allocations.Load() != 1 {
		t.Fatal("reviewed rental did not succeed")
	}
}
