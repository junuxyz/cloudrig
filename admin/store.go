package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"golang.org/x/sys/unix"
)

type Store struct {
	Root, Run string
	Owner     Config
	Client    *http.Client
	Base      map[string]string
	Output    io.Writer
	readMu    *sync.Mutex
	reads     map[string]instanceRead
}

func newStore(root, run string, cfg Config) *Store {
	return &Store{
		Root:  root,
		Run:   run,
		Owner: cfg,
		Client: &http.Client{
			Transport:     &http.Transport{Proxy: nil},
			Timeout:       30 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
		Base:   map[string]string{"vastai": "https://console.vast.ai", "runpod": "https://api.runpod.io"},
		Output: os.Stderr,
		reads:  make(map[string]instanceRead),
		readMu: &sync.Mutex{},
	}
}

func (s *Store) readDB() (DB, error) {
	db := DB{Records: map[string]Record{}, Pending: map[string]bool{}, Requests: map[string]Record{}}
	data, e := os.ReadFile(filepath.Join(s.Root, "resources.json"))
	if os.IsNotExist(e) {
		return db, nil
	}
	if e != nil {
		return db, e
	}
	e = json.Unmarshal(data, &db)
	if db.Records == nil {
		db.Records = map[string]Record{}
	}
	if db.Pending == nil {
		db.Pending = map[string]bool{}
	}
	if db.Requests == nil {
		db.Requests = map[string]Record{}
	}
	return db, e
}

func writeJSON(path string, value any) error {
	data, e := json.MarshalIndent(value, "", "  ")
	if e != nil {
		return e
	}
	return atomic(path, append(data, '\n'), 0600)
}

func atomic(path string, data []byte, mode os.FileMode) error {
	f, e := os.CreateTemp(filepath.Dir(path), ".crig-")
	if e != nil {
		return e
	}
	defer os.Remove(f.Name())
	if e = f.Chmod(mode); e == nil {
		_, e = f.Write(data)
	}
	if e == nil {
		e = f.Sync()
	}
	closeErr := f.Close()
	if e == nil {
		e = closeErr
	}
	if e != nil {
		return e
	}
	return os.Rename(f.Name(), path)
}

func (s *Store) lock() (func(), error) {
	f, e := os.OpenFile(filepath.Join(s.Root, ".lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	if e = unix.Flock(int(f.Fd()), unix.LOCK_EX); e != nil {
		f.Close()
		return nil, e
	}
	return func() { unix.Flock(int(f.Fd()), unix.LOCK_UN); f.Close() }, nil
}

func keyOf(provider, id string) string { return provider + "-" + id }

func publicDir(root, provider, id string) string {
	digest := sha256.Sum256([]byte(provider + "/" + id))
	return filepath.Join(root, fmt.Sprintf("%x", digest[:16]))
}
