#!/usr/bin/env bash
export PATH="/usr/local/bin:$PATH"

bootstrap_tools() (
    set -eu

    minimum_rsync=$1

    remote_uid=$(id -u)
    if [ "$remote_uid" -eq 0 ]; then
        exec 9>/var/lock/cloudrig-bootstrap.lock
        flock 9
    fi

    rsync_ready() {
        version=$(rsync --version 2>/dev/null | sed -n '1s/^rsync *version *\([0-9][0-9.]*\).*/\1/p')
        [ -n "$version" ] && [ "$(printf '%s\n' "$minimum_rsync" "$version" | sort -V | head -n 1)" = "$minimum_rsync" ]
    }

    if rsync_ready && uv --version >/dev/null 2>&1; then
        exit 0
    fi

    if [ "$remote_uid" -ne 0 ]; then
        echo 'Automatic tool installation requires root SSH.' >&2
        exit 77
    fi

    export DEBIAN_FRONTEND=noninteractive
    bootstrap_dir=$(mktemp -d)
    trap 'rm -rf "$bootstrap_dir"' EXIT
    if ! rsync_ready; then
        apt-get update
        apt-get install -y --no-install-recommends ca-certificates curl build-essential pkg-config \
            libssl-dev libxxhash-dev libzstd-dev liblz4-dev zlib1g-dev libpopt-dev libacl1-dev libattr1-dev libidn2-dev
        rsync_version=3.5.1
        curl -fLsS --connect-timeout 15 --max-time 120 \
            "https://rsync.samba.org/ftp/rsync/src/rsync-$rsync_version.tar.gz" -o "$bootstrap_dir/rsync.tar.gz"
        printf '%s  %s\n' c55f9c9dc10fb8bec397b399a0fdded53cc9a2d8e30891bb0d63724d25c37bef \
            "$bootstrap_dir/rsync.tar.gz" | sha256sum -c -
        tar -xzf "$bootstrap_dir/rsync.tar.gz" -C "$bootstrap_dir"
        (
            cd "$bootstrap_dir/rsync-$rsync_version"
            ./configure --prefix=/usr/local --disable-md2man
            make -j "$(getconf _NPROCESSORS_ONLN)"
            make install
        )
    fi

    if ! uv --version >/dev/null 2>&1; then
        if ! command -v curl >/dev/null 2>&1; then
            apt-get update
            apt-get install -y --no-install-recommends ca-certificates curl
        fi
        installer=$bootstrap_dir/uv-install.sh
        curl -fLsS --connect-timeout 15 --max-time 120 https://astral.sh/uv/install.sh -o "$installer"
        UV_UNMANAGED_INSTALL=/usr/local/bin sh "$installer"
    fi

    rsync_ready
    uv --version
)

bootstrap_tools "$@"

# Add your project startup commands below.
