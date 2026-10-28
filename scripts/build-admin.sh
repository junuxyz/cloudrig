#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
mkdir -p dist
(cd admin && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w -buildid=' -o ../dist/cloudrig-admin .)
if command -v shasum >/dev/null; then
  shasum -a 256 dist/cloudrig-admin
else
  sha256sum dist/cloudrig-admin
fi
