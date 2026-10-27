package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"golang.org/x/term"
)

func (s *Store) login(provider string) error {
	if !validProvider(provider) {
		return errors.New("invalid provider")
	}
	if e := s.Confirm(fmt.Sprintf("LINK/ROTATE %s credentials\nThe new API key will be stored root-only. Existing SSH grants for this provider will be revoked.", provider)); e != nil {
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
	if !validProvider(provider) {
		return errors.New("invalid provider")
	}
	if len(token) < 1 || len(token) > 8192 || !regexp.MustCompile(`^[!-~]+$`).MatchString(token) {
		return errors.New("invalid token format")
	}
	unlock, e := s.lock()
	if e != nil {
		return e
	}
	defer unlock()
	tokenPath := filepath.Join(s.Root, provider+".token")
	previous, previousError := os.ReadFile(tokenPath)
	if e = atomic(tokenPath, []byte(token), 0600); e != nil {
		return e
	}
	if _, e = s.instances(provider); e != nil {
		if previousError == nil {
			atomic(tokenPath, previous, 0600)
		} else {
			os.Remove(tokenPath)
		}
		return errors.New("provider key validation failed; previous credentials were preserved")
	}
	db, e := s.readDB()
	if e != nil {
		return e
	}
	for key, rec := range db.Records {
		if rec.Provider == provider {
			rec.Revoked = true
			db.Records[key] = rec
		}
	}
	return writeJSON(filepath.Join(s.Root, "resources.json"), db)
}

func (s *Store) rent(a RentArgs) (string, error) {
	if !validProvider(a.Provider) || !namePattern.MatchString(a.Name) || !regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,511}$`).MatchString(a.Image) {
		return "", errors.New("invalid rental configuration")
	}
	search := Request{Op: "offers", Provider: a.Provider, ID: a.ID, Query: a.Query, Disk: a.Disk, Count: a.Count}
	offers, e := s.offers(search)
	if e != nil {
		return "", e
	}
	var chosen *Offer
	for _, o := range offers {
		if o.ID == a.ID {
			copy := o
			chosen = &copy
			break
		}
	}
	if chosen == nil {
		return "", fmt.Errorf("offer %s is unavailable for the selected GPU count and disk; refresh the market. No rental was submitted", a.ID)
	}
	o := *chosen
	price := o.Hourly
	extra := " + storage (not quoted)"
	if o.DiskRate != nil {
		price += float64(a.Disk) * (*o.DiskRate)
		extra = " including selected disk"
	}
	if !validNumber(price) {
		return "", errors.New("invalid quote")
	}
	quote := fmt.Sprintf("RENT %s\n"+
		"Provider: %s\n"+
		"GPU: %s x%d, %.1f GB each\n"+
		"Location: %s\n"+
		"Disk: %d GB\n"+
		"Image: %s\n"+
		"Current quote: $%.6f/hour%s\n"+
		"SSH access to this instance is included in this approval.\nNo automatic shutdown. Billing begins on allocation.",
		a.Name, a.Provider, o.GPU, o.Count, o.VRAM, o.Region, a.Disk, a.Image, price, extra)
	if e = s.Confirm(quote); e != nil {
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
			changed := current.Count != o.Count || current.GPU != o.GPU || current.VRAM != o.VRAM || current.Region != o.Region || current.Cloud != o.Cloud
			if changed || current.DiskRate == nil && o.DiskRate != nil || !validNumber(cost) || cost > price+0.000001 {
				return "", errors.New("price or GPU conditions changed; obtain a new approval")
			}
			freshFound = true
			break
		}
	}
	if !freshFound {
		return "", errors.New("offer disappeared; obtain a new approval")
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
		parts := strings.Split(a.ID, "|")
		if len(parts) != 3 {
			return "", errors.New("invalid RunPod offer")
		}
		data, e = s.call("runpod", "POST", "/v2/pods", map[string]any{
			"name": a.Name, "image": a.Image, "disk": a.Disk,
			"gpu":   map[string]any{"id": parts[0], "count": o.Count},
			"cloud": parts[1], "dataCenterIds": []string{parts[2]},
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
	if e = writeJSON(filepath.Join(s.Root, "resources.json"), db); e != nil {
		return id, e
	}
	fmt.Fprintf(os.Stderr, "Allocated %s. SSH setup will continue automatically; the instance is billable.\n", id)
	return id, nil
}

func (s *Store) mutate(provider, id, action string) error {
	if !idPattern.MatchString(id) || !validProvider(provider) {
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

	if e = s.Confirm(message); e != nil {
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
			return errors.New("resume price increased; obtain a new approval")
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
