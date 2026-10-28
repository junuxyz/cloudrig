//go:build darwin

package main

import (
	"net"

	"golang.org/x/sys/unix"
)

func peerUID(c *net.UnixConn) (uint32, error) {
	raw, e := c.SyscallConn()
	if e != nil {
		return 0, e
	}
	var uid uint32
	var err error
	e = raw.Control(func(fd uintptr) {
		cred, x := unix.GetsockoptXucred(int(fd), unix.SOL_LOCAL, unix.LOCAL_PEERCRED)
		err = x
		if x == nil {
			uid = cred.Uid
		}
	})
	if e != nil {
		return 0, e
	}
	return uid, err
}
