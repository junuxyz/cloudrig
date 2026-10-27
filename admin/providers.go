package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"unicode"
)

func validProvider(p string) bool { return p == "vastai" || p == "runpod" }

func validNumber(n float64) bool { return !math.IsNaN(n) && !math.IsInf(n, 0) && n >= 0 }

func text(v any) string {
	if v == nil {
		return ""
	}
	switch x := v.(type) {
	case string:
		return x
	case float64:
		return strconv.FormatFloat(x, 'f', -1, 64)
	}
	return ""
}

func num(v any) float64 {
	switch x := v.(type) {
	case float64:
		return x
	case string:
		if n, e := strconv.ParseFloat(x, 64); e == nil {
			return n
		}
		return math.NaN()
	}
	if v != nil {
		return math.NaN()
	}
	return 0
}

func object(v any) map[string]any {
	m, _ := v.(map[string]any)
	if m == nil {
		return map[string]any{}
	}
	return m
}

func array(v any) []any { x, _ := v.([]any); return x }

func fallback(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

func safeLabel(v string) bool {
	if len(v) > 1024 {
		return false
	}
	for _, c := range v {
		if unicode.IsControl(c) || unicode.Is(unicode.Cf, c) {
			return false
		}
	}
	return true
}

func (s *Store) call(p, method, path string, body any) (any, error) {
	if !validProvider(p) || !strings.HasPrefix(path, "/api/") && p == "vastai" || !strings.HasPrefix(path, "/v2/") && p == "runpod" {
		return nil, errors.New("invalid provider path")
	}
	token, e := os.ReadFile(filepath.Join(s.Root, p+".token"))
	if e != nil {
		return nil, errors.New("provider is not linked; run crig login with sudo authorization")
	}
	var reader io.Reader
	if body != nil {
		data, e := json.Marshal(body)
		if e != nil {
			return nil, e
		}
		reader = bytes.NewReader(data)
	}
	req, e := http.NewRequest(method, s.Base[p]+path, reader)
	if e != nil {
		return nil, e
	}
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(token)))
	req.Header.Set("Content-Type", "application/json")
	resp, e := s.Client.Do(req)
	if e != nil {
		return nil, errors.New("provider connection failed; check rented instances before repeating a mutation")
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("provider returned HTTP %d; response details withheld", resp.StatusCode)
	}
	if resp.StatusCode == 204 {
		return map[string]any{}, nil
	}
	var result any
	if e = json.NewDecoder(io.LimitReader(resp.Body, 32<<20)).Decode(&result); e != nil {
		return nil, errors.New("invalid provider response")
	}
	if object(result)["success"] == false {
		return nil, errors.New("provider declined operation; check account before retrying")
	}
	return result, nil
}

func (s *Store) offers(r Request) ([]Offer, error) {
	if !validProvider(r.Provider) || len(r.Query) > 128 || !safeLabel(r.Query) || r.Disk < 1 || r.Disk > 100000 || r.Count < 1 || r.Count > 64 {
		return nil, errors.New("invalid search")
	}
	out := []Offer{}
	if r.Provider == "vastai" {
		q := map[string]any{
			"verified":          map[string]any{"eq": true},
			"rentable":          map[string]any{"eq": true},
			"rented":            map[string]any{"eq": false},
			"external":          map[string]any{"eq": false},
			"num_gpus":          map[string]any{"gte": r.Count},
			"disk_space":        map[string]any{"gte": r.Disk},
			"direct_port_count": map[string]any{"gte": 1},
			"type":              "on-demand",
			"order":             [][]string{{"dph_total", "asc"}},
			"limit":             10000,
			"allocated_storage": r.Disk,
		}
		if r.ID != "" {
			id, e := strconv.ParseUint(r.ID, 10, 64)
			if e != nil || id == 0 {
				return nil, errors.New("invalid Vast offer ID")
			}
			q["ask_contract_id"] = map[string]any{"eq": id}
		} else {
			v, e := s.call("vastai", "GET", "/api/v0/gpu_names/unique/", nil)
			if e != nil {
				return nil, e
			}
			names := []string{}
			for _, v := range array(object(v)["gpu_names"]) {
				name := text(v)
				if strings.Contains(strings.ToLower(name), strings.ToLower(r.Query)) {
					names = append(names, name)
				}
			}
			if len(names) == 0 {
				return out, nil
			}
			q["gpu_name"] = map[string]any{"in": names}
		}
		v, e := s.call("vastai", "POST", "/api/v0/bundles/", q)
		if e != nil {
			return nil, e
		}
		for _, v := range array(object(v)["offers"]) {
			row := object(v)
			o := Offer{
				ID:           text(row["id"]),
				Provider:     "vastai",
				GPU:          strings.ReplaceAll(text(row["gpu_name"]), "_", " "),
				Count:        int(num(row["num_gpus"])),
				VRAM:         num(row["gpu_ram"]) / 1024,
				Hourly:       num(row["dph_base"]),
				Region:       fallback(text(row["geolocation"]), "Unknown"),
				Availability: "available",
			}
			if !idPattern.MatchString(o.ID) || row["dph_base"] == nil && row["dph_total"] == nil {
				return nil, errors.New("invalid offer or missing price")
			}
			if row["storage_cost"] != nil {
				n := num(row["storage_cost"]) / 720
				o.DiskRate = &n
			}
			if row["dph_base"] == nil {
				o.Hourly = num(row["dph_total"])
				if o.DiskRate != nil {
					o.Hourly -= float64(r.Disk) * (*o.DiskRate)
				}
			}
			if row["disk_space"] != nil {
				n := num(row["disk_space"])
				o.DiskMax = &n
			}
			if row["reliability2"] != nil {
				n := num(row["reliability2"])
				o.Reliability = &n
			}
			out = append(out, o)
		}
	} else {
		for _, cloud := range []string{"SECURE", "COMMUNITY"} {
			v, e := s.call("runpod", "GET", fmt.Sprintf("/v2/catalog/gpus?include=AVAILABILITY&product=POD&count=%d&cloud=%s", r.Count, cloud), nil)
			if e != nil {
				return nil, e
			}
			for _, v := range array(object(v)["gpus"]) {
				row := object(v)
				if !strings.Contains(strings.ToLower(text(row["name"])), strings.ToLower(r.Query)) ||
					row[strings.ToLower(cloud)] != true || text(row["availability"]) == "NONE" ||
					num(object(row["maxCount"])[strings.ToLower(cloud)]) < float64(r.Count) {
					continue
				}
				price := object(row["price"])[strings.ToLower(cloud)]
				if price == nil {
					continue
				}
				for _, v := range array(row["dataCenters"]) {
					c := object(v)
					if text(c["availability"]) == "NONE" {
						continue
					}
					out = append(out, Offer{
						ID:           text(row["id"]) + "|" + cloud + "|" + text(c["id"]),
						Provider:     "runpod",
						GPU:          text(row["name"]),
						Count:        r.Count,
						VRAM:         num(row["memory"]),
						Hourly:       num(price) * float64(r.Count),
						Region:       text(c["id"]),
						Cloud:        cloud,
						Availability: strings.ToLower(text(c["availability"])),
					})
				}
			}
		}
	}
	for _, o := range out {
		if !validNumber(o.Hourly) || o.VRAM <= 0 || !validNumber(o.VRAM) || o.Count < 1 ||
			!safeLabel(o.GPU) || !safeLabel(o.Region) ||
			o.DiskRate != nil && !validNumber(*o.DiskRate) ||
			o.DiskMax != nil && (!validNumber(*o.DiskMax) || *o.DiskMax < float64(r.Disk)) {
			return nil, errors.New("invalid provider quote")
		}
	}
	return out, nil
}

func (s *Store) instances(provider string) ([]Instance, error) {
	result := []Instance{}
	cursor := ""
	seen := map[string]bool{}
	for {
		var path string
		if provider == "vastai" {
			path = "/api/v1/instances/?limit=100&after_token=" + url.QueryEscape(cursor)
		} else {
			path = "/v2/pods?limit=100&cursor=" + url.QueryEscape(cursor)
		}
		v, e := s.call(provider, "GET", path, nil)
		if e != nil {
			return nil, e
		}
		data := object(v)
		field := "instances"
		if provider == "runpod" {
			field = "pods"
		}
		for _, v := range array(data[field]) {
			i, e := parseInstance(provider, object(v))
			if e != nil {
				return nil, e
			}
			result = append(result, i)
		}
		if provider == "vastai" {
			cursor = text(data["next_token"])
		} else {
			cursor = text(object(data["pagination"])["nextCursor"])
		}
		if cursor == "" {
			return result, nil
		}
		if seen[cursor] {
			return nil, errors.New("provider repeated pagination cursor")
		}
		seen[cursor] = true
	}
}

func parseInstance(provider string, row map[string]any) (Instance, error) {
	i := Instance{Provider: provider, ID: text(row["id"]), User: "root", Direct: true}
	if !idPattern.MatchString(i.ID) {
		return i, errors.New("invalid instance identifier")
	}
	if provider == "vastai" {
		i.Name = fallback(text(row["label"]), i.ID)
		i.GPU = strings.ReplaceAll(text(row["gpu_name"]), "_", " ")
		i.Count = int(num(row["num_gpus"]))
		i.Status = strings.ToLower(text(row["actual_status"]))
		i.Hourly = num(row["dph_total"])
		i.ContractRate = num(row["dph_base"])
		i.Region = text(row["geolocation"])
		ports := array(object(row["ports"])["22/tcp"])
		if len(ports) > 0 {
			i.Host = text(row["public_ipaddr"])
			i.Port = int(num(object(ports[0])["HostPort"]))
		} else {
			i.Host = text(row["ssh_host"])
			i.Port = int(num(row["ssh_port"]))
		}
	} else {
		i.Name = fallback(text(row["name"]), i.ID)
		i.GPU = text(object(row["gpu"])["id"])
		i.Count = int(num(object(row["gpu"])["count"]))
		i.Status = strings.ToLower(text(row["status"]))
		i.Hourly = num(row["cost"])
		i.Region = text(row["dataCenterId"])
		ssh := object(row["ssh"])
		endpoint := object(ssh["direct"])
		if len(endpoint) == 0 {
			i.Direct = false
			endpoint = object(ssh["proxy"])
		}
		i.Host = text(endpoint["host"])
		i.Port = int(num(endpoint["port"]))
		i.User = fallback(text(endpoint["username"]), "root")
	}
	if i.Count < 1 {
		i.Count = 1
	}
	if !safeLabel(i.Name) || !safeLabel(i.GPU) || !validNumber(i.Hourly) || !validNumber(i.ContractRate) {
		return i, errors.New("invalid instance metadata")
	}
	return i, nil
}

func (s *Store) instance(provider, id string) (Instance, error) {
	if !idPattern.MatchString(id) {
		return Instance{}, errors.New("invalid ID")
	}
	if provider == "runpod" {
		v, e := s.call(provider, "GET", "/v2/pods/"+id, nil)
		if e != nil {
			return Instance{}, e
		}
		return parseInstance(provider, object(v))
	}
	rows, e := s.instances(provider)
	if e != nil {
		return Instance{}, e
	}
	for _, i := range rows {
		if i.ID == id {
			return i, nil
		}
	}
	return Instance{}, errors.New("instance not found")
}

func (s *Store) transition(p, id, action string) (any, error) {
	if !idPattern.MatchString(id) {
		return nil, errors.New("invalid ID")
	}
	if p == "vastai" {
		method := "PUT"
		path := "/api/v0/instances/" + id + "/"
		body := any(map[string]string{"state": map[string]string{"start": "running", "stop": "stopped"}[action]})
		if action == "destroy" {
			method = "DELETE"
			body = nil
		}
		return s.call(p, method, path, body)
	}
	if action == "destroy" {
		return s.call(p, "DELETE", "/v2/pods/"+id, nil)
	}
	return s.call(p, "POST", "/v2/pods/"+id+"/action", map[string]string{"action": action})
}

func (s *Store) resumeQuote(provider, id string) (float64, error) {
	if provider == "vastai" {
		i, e := s.instance(provider, id)
		if e != nil {
			return 0, e
		}
		if i.ContractRate <= 0 || !validNumber(i.ContractRate) {
			return 0, errors.New("existing contract has no verifiable compute quote; refusing billable start")
		}
		return i.ContractRate, nil
	}
	raw, e := s.call("runpod", "GET", "/v2/pods/"+id, nil)
	if e != nil {
		return 0, e
	}
	pod := object(raw)
	gpu := object(pod["gpu"])
	count := num(gpu["count"])
	if !validNumber(count) || count < 1 || count > 64 || math.Trunc(count) != count {
		return 0, errors.New("cannot verify the pod GPU count; refusing billable start")
	}
	cloud := strings.ToLower(text(pod["cloud"]))
	if cloud != "secure" && cloud != "community" {
		return 0, errors.New("unknown pod cloud tier")
	}
	catalog, e := s.call("runpod", "GET", "/v2/catalog/gpus", nil)
	if e != nil {
		return 0, e
	}
	for _, entry := range array(object(catalog)["gpus"]) {
		row := object(entry)
		if text(row["id"]) == text(gpu["id"]) {
			price := num(object(row["price"])[cloud]) * count
			if validNumber(price) && price > 0 {
				return price, nil
			}
		}
	}
	return 0, errors.New("cannot establish the current compute quote for this pod")
}
