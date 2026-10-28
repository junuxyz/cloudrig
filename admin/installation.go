package main

import (
	"encoding/json"
	"errors"
	"net"
	"path/filepath"
	"time"
)

func (s *Store) checkService() error {
	c, err := net.DialTimeout("unix", filepath.Join(s.Run, "api.sock"), time.Second)
	if err != nil {
		return err
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(time.Second))
	if err = json.NewEncoder(c).Encode(Request{Op: "health", Provider: "vastai"}); err != nil {
		return err
	}
	var reply struct {
		Data struct {
			Protocol int `json:"protocol"`
		} `json:"data"`
		Error string `json:"error"`
	}
	if err = json.NewDecoder(c).Decode(&reply); err != nil {
		return err
	}
	if reply.Error != "" || reply.Data.Protocol != 3 {
		return errors.New("running Cloudrig service has an incompatible protocol")
	}
	return nil
}
