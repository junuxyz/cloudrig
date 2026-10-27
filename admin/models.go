package main

import (
	"errors"
	"regexp"
)

var idPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$`)

var namePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$`)

var errDenied = errors.New("operation requires fresh sudo authorization")

type Config struct {
	UID int `json:"uid"`
	GID int `json:"gid"`
}

type Record struct {
	Provider   string `json:"provider"`
	ID         string `json:"id"`
	Name       string `json:"name"`
	Host       string `json:"host"`
	Port       int    `json:"port"`
	User       string `json:"user"`
	Key        string `json:"key"`
	KnownHosts string `json:"known_hosts"`
	HostKey    string `json:"host_key"`
	Revoked    bool   `json:"revoked"`
	Connecting bool   `json:"connecting,omitempty"`
}

type DB struct {
	Records map[string]Record `json:"records"`
	Pending map[string]bool   `json:"pending"`
}

type Request struct {
	Op       string `json:"op"`
	Provider string `json:"provider"`
	ID       string `json:"id,omitempty"`
	Query    string `json:"query,omitempty"`
	Count    int    `json:"count,omitempty"`
	Disk     int    `json:"disk,omitempty"`
}

type Offer struct {
	ID           string   `json:"id"`
	Provider     string   `json:"provider"`
	GPU          string   `json:"gpu"`
	Count        int      `json:"count"`
	VRAM         float64  `json:"vram"`
	Hourly       float64  `json:"hourly"`
	Region       string   `json:"region"`
	Availability string   `json:"availability"`
	Cloud        string   `json:"cloud"`
	DiskRate     *float64 `json:"disk_hourly_gb"`
	DiskMax      *float64 `json:"disk_max"`
	Reliability  *float64 `json:"reliability"`
}

type Instance struct {
	ID           string  `json:"id"`
	Provider     string  `json:"provider"`
	Name         string  `json:"name"`
	GPU          string  `json:"gpu"`
	Count        int     `json:"count"`
	Status       string  `json:"status"`
	Hourly       float64 `json:"hourly"`
	Region       string  `json:"region"`
	Host         string  `json:"host"`
	Port         int     `json:"port"`
	User         string  `json:"user"`
	Direct       bool    `json:"direct"`
	PublicKey    string  `json:"public_key,omitempty"`
	AgentSocket  string  `json:"agent_socket,omitempty"`
	KnownHosts   string  `json:"known_hosts,omitempty"`
	Approved     bool    `json:"approved"`
	ContractRate float64 `json:"-"`
}

type RentArgs struct {
	Provider, ID, Query, Name, Image string
	Disk, Count                      int
}
