package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"time"
)

func (s *Store) public(r Request) (any, error) {
	if !validProvider(r.Provider) {
		return nil, errors.New("unknown provider")
	}
	switch r.Op {
	case "offers":
		return s.offers(r)
	case "instances":
		rows, e := s.instances(r.Provider)
		if e != nil {
			return nil, e
		}
		for n, i := range rows {
			rows[n] = s.publicGrant(i)
		}
		return rows, nil
	case "instance":
		i, e := s.instance(r.Provider, r.ID)
		if e != nil {
			return nil, e
		}
		if e = s.finishConnection(i); e != nil {
			return nil, e
		}
		return s.publicGrant(i), nil
	case "stop":
		if _, e := s.approved(r.Provider, r.ID); e != nil {
			return nil, e
		}
		return s.transition(r.Provider, r.ID, "stop")
	default:
		return nil, errDenied
	}
}

func socketListener(path string, uid, gid int) (*net.UnixListener, error) {
	os.Remove(path)
	addr := &net.UnixAddr{Name: path, Net: "unix"}
	l, e := net.ListenUnix("unix", addr)
	if e != nil {
		return nil, e
	}
	if e = os.Chmod(path, 0600); e == nil {
		e = os.Chown(path, uid, gid)
	}
	if e != nil {
		l.Close()
		return nil, e
	}
	return l, nil
}

func (s *Store) serveAPI(l *net.UnixListener) {
	for {
		c, e := l.AcceptUnix()
		if e != nil {
			return
		}
		go func() {
			defer c.Close()
			if uid, e := peerUID(c); e != nil || uid != uint32(s.Owner.UID) && uid != 0 {
				return
			}
			c.SetDeadline(time.Now().Add(60 * time.Second))
			decoder := json.NewDecoder(io.LimitReader(c, 16384))
			decoder.DisallowUnknownFields()
			var r Request
			var data any
			var err error
			if e := decoder.Decode(&r); e != nil {
				err = errors.New("invalid request")
			} else {
				data, err = s.public(r)
			}
			reply := map[string]any{"data": data}
			if err != nil {
				reply = map[string]any{"error": err.Error()}
			}
			json.NewEncoder(c).Encode(reply)
		}()
	}
}

func (s *Store) daemon(ctx context.Context) error {
	if e := os.MkdirAll(s.Run, 0755); e != nil {
		return e
	}
	l, e := socketListener(filepath.Join(s.Run, "api.sock"), s.Owner.UID, s.Owner.GID)
	if e != nil {
		return e
	}
	defer l.Close()
	go s.serveAPI(l)
	type signer struct {
		stop      func()
		signature string
	}
	running := map[string]signer{}
	defer func() {
		for _, item := range running {
			item.stop()
		}
	}()
	for {
		db, e := s.readDB()
		if e != nil {
			return e
		}
		for key, current := range running {
			rec, exists := db.Records[key]
			data, _ := json.Marshal(rec)
			if !exists || rec.Revoked || current.signature != string(data) {
				current.stop()
				delete(running, key)
			}
		}
		for key, rec := range db.Records {
			if rec.Revoked || rec.Connecting {
				continue
			}
			if _, exists := running[key]; !exists {
				if stop, e := s.startSigner(rec); e == nil {
					data, _ := json.Marshal(rec)
					running[key] = signer{stop, string(data)}
				}
			}
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(time.Second):
		}
	}
}
