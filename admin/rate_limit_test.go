package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync"
	syncatomic "sync/atomic"
	"testing"
	"time"
)

func TestStopRequiresPrivilegedPathAndSendsOneRequest(t *testing.T) {
	s := testStore(t)
	var stops syncatomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case "GET":
			w.Write([]byte(`{"instances":[{"id":1,"label":"training","gpu_name":"RTX 3060","actual_status":"running","num_gpus":1}]}`))
		case "PUT":
			var body map[string]string
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body["state"] != "stopped" {
				t.Error("stop sent an incorrect request", body, err)
			}
			stops.Add(1)
			w.Write([]byte(`{"success":true}`))
		default:
			t.Error("unexpected request", r.Method)
			w.WriteHeader(500)
		}
	}))
	defer server.Close()
	s.Base["vastai"] = server.URL
	if _, err := s.public(Request{Op: "stop", Provider: "vastai", ID: "1"}); err == nil || stops.Load() != 0 {
		t.Fatal("ordinary API stopped an instance", err)
	}
	if err := s.mutate("vastai", "1", "stop"); err != nil || stops.Load() != 1 {
		t.Fatal("privileged stop did not send exactly one request", err, stops.Load())
	}
}

func TestPublicInstanceReadsShareOneRequest(t *testing.T) {
	s := testStore(t)
	var calls syncatomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Write([]byte(`{"instances":[{"id":1,"label":"training","gpu_name":"RTX 3060","actual_status":"loading","num_gpus":1}]}`))
	}))
	defer server.Close()
	s.Base["vastai"] = server.URL
	var group sync.WaitGroup
	for range 8 {
		group.Add(1)
		go func() {
			defer group.Done()
			if _, err := s.public(Request{Op: "instances", Provider: "vastai"}); err != nil {
				t.Error(err)
			}
		}()
	}
	group.Wait()
	if calls.Load() != 1 {
		t.Fatal("parallel readers repeated the account listing", calls.Load())
	}
	rows, err := s.readInstances("vastai")
	if err != nil {
		t.Fatal(err)
	}
	rows[0].Name = "changed by caller"
	rows, _ = s.readInstances("vastai")
	if rows[0].Name != "training" {
		t.Fatal("caller changed the cached account listing")
	}
	if _, err := s.instances("vastai"); err != nil || calls.Load() != 2 {
		t.Fatal("fresh privileged reads used a cached listing", calls.Load(), err)
	}
}

func TestRateLimitCooldownAndMutationDoNotRepeatProviderRequests(t *testing.T) {
	s := testStore(t)
	var calls syncatomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Retry-After", "45")
		w.WriteHeader(http.StatusTooManyRequests)
		w.Write([]byte(`private provider response`))
	}))
	defer server.Close()
	s.Base["vastai"] = server.URL
	for range 2 {
		_, err := s.readInstances("vastai")
		var limited *rateLimitError
		if !errors.As(err, &limited) || limited.seconds != 45 {
			t.Fatal("lost retry delay", err)
		}
	}
	if calls.Load() != 1 {
		t.Fatal("cooldown still called the provider", calls.Load())
	}
	if _, err := s.call("vastai", "PUT", "/api/v0/asks/1/", nil); err == nil || calls.Load() != 2 {
		t.Fatal("mutation was repeated or skipped", calls.Load(), err)
	}
	if delay := time.Until(s.reads["vastai"].until); delay < 40*time.Second {
		t.Fatal("cooldown did not honor Retry-After", delay)
	}
}
