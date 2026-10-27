package main

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"golang.org/x/term"
)

func ttyConfirm(message string) error {
	f, e := os.OpenFile("/dev/tty", os.O_RDWR, 0)
	if e != nil {
		return errors.New("run this sudo action in a human-owned terminal")
	}
	defer f.Close()
	if !term.IsTerminal(int(f.Fd())) {
		return errors.New("explicit approval requires a terminal")
	}
	return requestApproval(f, message)
}

func requestApproval(terminal io.ReadWriter, message string) error {
	var nonce [6]byte
	if _, e := rand.Read(nonce[:]); e != nil {
		return errors.New("could not create a one-operation approval request")
	}
	expected := "APPROVE " + hex.EncodeToString(nonce[:])
	if _, e := fmt.Fprintf(terminal, "%s\nApproval applies only to this operation.\nType %s to submit, or press Enter to cancel:\n", message, expected); e != nil {
		return e
	}
	line, e := bufio.NewReader(io.LimitReader(terminal, 64)).ReadString('\n')
	if e != nil || strings.TrimSpace(line) != expected {
		return errors.New("cancelled; explicit approval for this operation is required")
	}
	return nil
}
