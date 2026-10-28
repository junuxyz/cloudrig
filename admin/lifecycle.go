package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"golang.org/x/term"
)

func (s *Store) login(provider string) error {
	if !validProvider(provider) {
		return errors.New("invalid provider")
	}
	if _, e := fmt.Fprintf(s.Output, "LINK/ROTATE %s credentials\nThe new API key will be stored root-only. Existing SSH grants for this provider will be revoked.\n", provider); e != nil {
		return e
	}
	f, e := os.OpenFile("/dev/tty", os.O_RDWR, 0)
	if e != nil {
		return e
	}
	fmt.Fprintf(f, "%s API key (stored root-only, input hidden): ", provider)
	secret, e := term.ReadPassword(int(f.Fd()))
	fmt.Fprintln(f)
	f.Close()
	if e != nil {
		return errors.New("hidden terminal input unavailable")
	}
	token := strings.TrimSpace(string(secret))
	return s.linkToken(provider, token)
}

func (s *Store) linkToken(provider, token string) error {
	if len(token) > 8192 || !tokenPattern.MatchString(token) {
		return errors.New("invalid token format")
	}
	unlock, e := s.lock()
	if e != nil {
		return e
	}
	defer unlock()
	db, e := s.readDB()
	if e != nil {
		return e
	}
	staging, e := os.MkdirTemp(s.Root, ".validate-key-")
	if e != nil {
		return e
	}
	defer os.RemoveAll(staging)
	candidate := *s
	candidate.Root = staging
	if e = atomic(filepath.Join(staging, provider+".token"), []byte(token), 0600); e != nil {
		return e
	}
	if _, e = candidate.instances(provider); e != nil {
		return errors.New("provider key validation failed; existing credentials were not changed")
	}
	for key, rec := range db.Records {
		if rec.Provider == provider {
			rec.Revoked = true
			db.Records[key] = rec
		}
	}
	// Revoke old grants before another account's credential can become visible.
	if e = writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
		return e
	}
	return atomic(filepath.Join(s.Root, provider+".token"), []byte(token), 0600)
}

func (s *Store) rent(a RentArgs) (string, error) {
	if !validProvider(a.Provider) || !namePattern.MatchString(a.Name) || !imagePattern.MatchString(a.Image) {
		return "", errors.New("invalid rental configuration")
	}
	var runpodOffer []string
	if a.Provider == "runpod" {
		runpodOffer = strings.Split(a.ID, "|")
		if len(runpodOffer) != 3 {
			return "", errors.New("invalid RunPod offer")
		}
	}
	search := Request{Op: "offers", Provider: a.Provider, ID: a.ID, Query: a.Query, Disk: a.Disk, Count: a.Count}
	offers, e := s.offers(search)
	if e != nil {
		return "", e
	}
	var o Offer
	found := false
	for _, offer := range offers {
		if offer.ID == a.ID {
			o, found = offer, true
			break
		}
	}
	if !found {
		return "", fmt.Errorf("offer %s is unavailable for the selected GPU count and disk; refresh the market. No rental was submitted", a.ID)
	}
	price := o.Hourly
	extra := " + storage (not quoted)"
	if o.DiskRate != nil {
		price += float64(a.Disk) * (*o.DiskRate)
		extra = " including selected disk"
	}
	if !validNumber(price) {
		return "", errors.New("invalid quote")
	}
	if a.MaxHourly != nil && (!validNumber(*a.MaxHourly) || price > *a.MaxHourly+0.000001) {
		return "", errors.New("the current quote exceeds the reviewed price; refresh before renting")
	}
	quote := fmt.Sprintf("RENT %s\n"+
		"Provider: %s\n"+
		"GPU: %s x%d, %.1f GB each\n"+
		"Location: %s\n"+
		"Disk: %d GB\n"+
		"Image: %s\n"+
		"Current quote: $%.6f/hour%s",
		a.Name, a.Provider, o.GPU, o.Count, o.VRAM, o.Region, a.Disk, a.Image, price, extra)
	if _, e = fmt.Fprintln(s.Output, quote); e != nil {
		return "", e
	}
	unlock, e := s.lock()
	if e != nil {
		return "", e
	}
	defer unlock()
	refreshed, refreshError := s.offers(search)
	if refreshError != nil {
		return "", refreshError
	}
	freshFound := false
	for _, current := range refreshed {
		if current.ID == a.ID {
			cost := current.Hourly
			if current.DiskRate != nil {
				cost += float64(a.Disk) * (*current.DiskRate)
			}
			changed := current.Count != o.Count || current.GPU != o.GPU || current.VRAM != o.VRAM ||
				current.Region != o.Region ||
				current.Cloud != o.Cloud
			if changed || current.DiskRate == nil && o.DiskRate != nil || !validNumber(cost) ||
				cost > price+0.000001 {
				return "", errors.New("price or GPU conditions changed; refresh and retry")
			}
			freshFound = true
			break
		}
	}
	if !freshFound {
		return "", errors.New("offer disappeared; refresh and retry")
	}
	db, e := s.readDB()
	if e != nil {
		return "", e
	}
	pending := a.Provider + "/" + a.Name
	if db.Pending[pending] {
		return "", errors.New("a request with this name has an unknown outcome; inspect account before retrying")
	}
	for _, rec := range db.Records {
		if rec.Provider == a.Provider && rec.Name == a.Name {
			return "", errors.New("name already has a recorded allocation; inspect it before renting again")
		}
	}
	instances, e := s.instances(a.Provider)
	if e != nil {
		return "", e
	}
	for _, i := range instances {
		if i.Name == a.Name {
			return "", errors.New("name already rented")
		}
	}
	key, pub, e := s.newKey(a.Provider + "-" + a.Name + "-" + strconv.FormatInt(time.Now().UnixNano(), 10))
	if e != nil {
		return "", e
	}
	db.Pending[pending] = true
	db.Requests[pending] = Record{Provider: a.Provider, Name: a.Name, Key: key, Connecting: true}
	if e = writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
		return "", e
	}
	var data any
	if a.Provider == "vastai" {
		data, e = s.call("vastai", "PUT", "/api/v0/asks/"+a.ID+"/", map[string]any{
			"client_id": "me", "image": a.Image, "disk": a.Disk,
			"label": a.Name, "runtype": "ssh_direct", "cancel_unavail": true,
		})
	} else {
		data, e = s.call("runpod", "POST", "/v2/pods", map[string]any{
			"name": a.Name, "image": a.Image, "disk": a.Disk,
			"gpu":   map[string]any{"id": runpodOffer[0], "count": o.Count},
			"cloud": runpodOffer[1], "dataCenterIds": []string{runpodOffer[2]},
			"ports": []string{"22/tcp"}, "startSsh": true,
			"env": map[string]string{"PUBLIC_KEY": pub},
		})
	}
	if e != nil {
		return "", e
	}
	id := text(object(data)["id"])
	if a.Provider == "vastai" {
		id = text(object(data)["new_contract"])
	}
	if !idPattern.MatchString(id) {
		return "", errors.New("allocation outcome unknown; inspect rented instances")
	}
	rec := Record{Provider: a.Provider, ID: id, Name: a.Name, Key: key, Connecting: true}
	db.Records[keyOf(a.Provider, id)] = rec
	db.Pending[pending] = false
	delete(db.Requests, pending)
	if e = writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
		return id, e
	}
	fmt.Fprintf(os.Stderr, "Allocated %s.\n", id)
	return id, nil
}

// Recovery changes the journal and connection authority, never provider billing state.
func (s *Store) recoverRental(provider, name, id string) error {
	if !validProvider(provider) || !namePattern.MatchString(name) ||
		id != "-" && !idPattern.MatchString(id) {
		return errors.New("use recover PROVIDER NAME INSTANCE_ID, or '-' after verifying no allocation exists")
	}
	unlock, e := s.lock()
	if e != nil {
		return e
	}
	defer unlock()
	db, e := s.readDB()
	if e != nil {
		return e
	}
	pending := provider + "/" + name
	recorded := db.Pending[pending]
	if id == "-" {
		for _, rec := range db.Records {
			if rec.Provider == provider && rec.Name == name {
				recorded = true
			}
		}
	}
	if !recorded {
		return errors.New("this rental has no protected request to recover")
	}
	rows, e := s.instances(provider)
	if e != nil {
		return e
	}
	found := false
	for _, item := range rows {
		if item.Name != name {
			continue
		}
		if id == "-" {
			return errors.New("an instance still has this rental name; recover its ID instead of clearing")
		}
		if item.ID == id {
			found = true
		}
	}
	if id != "-" {
		if !found {
			return errors.New("the provider did not return that ID with this rental name; no record changed")
		}
		if _, exists := db.Records[keyOf(provider, id)]; exists {
			return errors.New("this instance already has a protected record; no record changed")
		}
		rec := db.Requests[pending]
		if rec.Key == "" {
			if provider == "runpod" {
				return errors.New("this older RunPod receipt has no saved key association; recover manually before clearing it")
			}
			key, _, err := s.newKey(keyOf(provider, id) + "-" + strconv.FormatInt(time.Now().UnixNano(), 10))
			if err != nil {
				return err
			}
			rec.Key = key
		}
		if _, e = os.ReadFile(rec.Key + ".pub"); e != nil {
			return e
		}
		rec.Provider, rec.Name, rec.ID, rec.Connecting = provider, name, id, true
		db.Records[keyOf(provider, id)] = rec
	}
	delete(db.Pending, pending)
	delete(db.Requests, pending)
	if id == "-" {
		for key, rec := range db.Records {
			if rec.Provider == provider && rec.Name == name {
				delete(db.Records, key)
			}
		}
	}
	return writeJSON(filepath.Join(s.Root, "resources.json"), db)
}

func (s *Store) mutate(provider, id, action string) error {
	if !validProvider(provider) {
		return errors.New("invalid resource")
	}
	i, e := s.instance(provider, id)
	if e != nil {
		return e
	}
	if action != "start" && action != "destroy" {
		return errDenied
	}
	message := fmt.Sprintf("%s %s/%s (%s)", strings.ToUpper(action), provider, id, i.Name)
	quoted := 0.0
	if action == "start" {
		quoted, e = s.resumeQuote(provider, id)
		if e != nil {
			return e
		}
		message += fmt.Sprintf("\nCurrent compute quote: $%.6f/hour. Storage is billed separately.", quoted)
	} else {
		message += "\nInstance-local files will be deleted."
	}

	if _, e = fmt.Fprintln(s.Output, message); e != nil {
		return e
	}
	unlock, e := s.lock()
	if e != nil {
		return e
	}
	defer unlock()
	db, e := s.readDB()
	if e != nil {
		return e
	}
	if action == "start" {
		fresh, e := s.resumeQuote(provider, id)
		if e != nil {
			return e
		}
		if fresh > quoted+0.000001 {
			return errors.New("resume price increased; retry to obtain a fresh quote")
		}
	}
	if action == "destroy" {
		if rec, ok := db.Records[keyOf(provider, id)]; ok {
			rec.Revoked = true
			db.Records[keyOf(provider, id)] = rec
			if e = writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
				return e
			}
		}
	}
	_, e = s.transition(provider, id, action)
	if e != nil {
		return e
	}
	if action == "destroy" {
		delete(db.Records, keyOf(provider, id))
		return writeJSON(filepath.Join(s.Root, "resources.json"), db)
	}
	if action == "start" {
		if rec, ok := db.Records[keyOf(provider, id)]; ok && !rec.Revoked {
			rec.Connecting = true
			db.Records[keyOf(provider, id)] = rec
			return writeJSON(filepath.Join(s.Root, "resources.json"), db)
		}
	}
	return nil
}
