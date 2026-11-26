# Run on the instance. Exit 75 while another probe should wait for preparation.
set -eu
umask 077
commit=$1
case "$(uname -m)" in
    x86_64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) exit 0 ;;
esac
[ ! -f /etc/alpine-release ] && command -v curl >/dev/null && command -v flock >/dev/null || exit 0

root="$HOME/.vscode-server"
cli="$root/code-$commit"
cache="$root/cli/servers"
target="$cache/Stable-$commit"
ready() { [ -d "$target" ] && [ -x "$cli" ]; }
ready && exit 0
mkdir -p "$cache" || exit 0

# Acquire the lock before returning to the boot script. Its child inherits the
# descriptor, so a second SSH connection can tell when the download is finished.
exec 9>>"$cache/.cloudrig-$commit.lock"
flock -n 9 || exit 75
ready && exit 0
[ ! -f "$cache/.cloudrig-$commit.failed" ] || exit 0

prepare() {
    set -eu
    tmp=$(mktemp -d "$cache/.cloudrig-XXXXXXXX")
    trap 'rm -rf "$tmp"' EXIT
    download() {
        url="https://update.code.visualstudio.com/commit:$commit/$1/stable"
        curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
            --retry 1 --max-time 65 --connect-timeout 8 "$url" -o "$tmp/archive"
    }
    if [ ! -x "$cli" ]; then
        download "cli-alpine-$arch"
        tar -xzf "$tmp/archive" -C "$tmp" code
        "$tmp/code" --version | grep -Fq "$commit"
        mv -T "$tmp/code" "$cli"
    fi
    if [ ! -d "$target" ]; then
        download "server-linux-$arch"
        mkdir -p "$tmp/build/server"
        tar -xzf "$tmp/archive" -C "$tmp/build/server" --strip-components=1
        "$tmp/build/server/bin/code-server" --version | grep -Fxq "$commit"
        mv -T "$tmp/build" "$target"
    fi
}

# Remote SSH can perform its normal installation if this optional download fails.
(
    trap '' HUP
    set +e
    (prepare)
    if [ "$?" -ne 0 ]; then touch "$cache/.cloudrig-$commit.failed"; fi
) </dev/null >/dev/null 2>&1 &
exit 75
