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

type instanceRead struct {
	rows  []Instance
	err   error
	until time.Time
}

// Only public reads share snapshots. Paid operations and recovery still read fresh state.
func (s *Store) readInstances(provider string) ([]Instance, error) {
	s.readMu.Lock()
	defer s.readMu.Unlock()
	cached := s.reads[provider]
	if time.Now().Before(cached.until) {
		return append([]Instance{}, cached.rows...), cached.err
	}
	rows, err := s.instances(provider)
	if err == nil {
		s.reads[provider] = instanceRead{rows: rows, until: time.Now().Add(5 * time.Second)}
	} else {
		var limited *rateLimitError
		if errors.As(err, &limited) {
			s.reads[provider] = instanceRead{err: err, until: time.Now().Add(time.Duration(limited.seconds) * time.Second)}
		}
	}
	return append([]Instance{}, rows...), err
}

func (s *Store) public(r Request) (any, error) {
	if !validProvider(r.Provider) {
		return nil, errors.New("unknown provider")
	}
	switch r.Op {
	case "health":
		return map[string]int{"protocol": 3}, nil
	case "rental-status":
		if !namePattern.MatchString(r.Query) {
			return nil, errors.New("invalid rental name")
		}
		unlock, e := s.lock()
		if e != nil {
			return nil, e
		}
		defer unlock()
		db, e := s.readDB()
		if e != nil {
			return nil, e
		}
		if db.Pending[r.Provider+"/"+r.Query] {
			return map[string]string{"state": "pending"}, nil
		}
		for _, rec := range db.Records {
			if rec.Provider == r.Provider && rec.Name == r.Query {
				return map[string]string{"state": "allocated", "id": rec.ID}, nil
			}
		}
		return map[string]string{"state": "absent"}, nil
	case "offers":
		return s.offers(r)
	case "instances":
		rows, e := s.readInstances(r.Provider)
		if e != nil {
			return nil, e
		}
		for n, i := range rows {
			rows[n] = s.publicGrant(i)
		}
		return rows, nil
	case "instance":
		if !idPattern.MatchString(r.ID) {
			return nil, errors.New("invalid instance identifier")
		}
		if r.Provider == "runpod" {
			i, e := s.instance(r.Provider, r.ID)
			if e != nil {
				return nil, e
			}
			if e = s.finishConnection(i); e != nil {
				return nil, e
			}
			return s.publicGrant(i), nil
		}
		rows, e := s.readInstances(r.Provider)
		if e != nil {
			return nil, e
		}
		for _, i := range rows {
			if i.ID == r.ID {
				if e = s.finishConnection(i); e != nil {
					return nil, e
				}
				return s.publicGrant(i), nil
			}
		}
		return nil, errors.New("instance not found")
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
				var limited *rateLimitError
				if errors.As(err, &limited) {
					reply["retry_after"] = limited.seconds
				}
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
		stop   func()
		record Record
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
			if !exists || rec.Revoked || current.record != rec {
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
					running[key] = signer{stop, rec}
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
