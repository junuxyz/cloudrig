#!/bin/sh
# Run this reviewed installer from your own terminal, not through an agent-owned password prompt.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077
if [ "$(id -u)" != 0 ] || [ -z "${SUDO_UID:-}" ] || [ "$SUDO_UID" = 0 ]; then
  echo 'Run: sudo -k sh scripts/install-admin.sh dist/cloudrig-admin' >&2
  exit 1
fi
case "$SUDO_UID" in *[!0-9]*|'') exit 1;; esac
owner_name=$(id -nu "$SUDO_UID")
owner_gid=$(id -g "$owner_name")
case "$owner_name" in *[!A-Za-z0-9_.-]*|'') echo 'Unsupported local account name.' >&2; exit 1;; esac
source_binary=${1:-dist/cloudrig-admin}
if [ ! -f "$source_binary" ] || [ -L "$source_binary" ]; then echo 'Build the native helper first.' >&2; exit 1; fi
effective_sudo=$(/usr/bin/sudo -l -U "$owner_name") || {
  echo 'Cannot inspect existing sudo policy; refusing installation.' >&2
  exit 1
}
if printf '%s\n' "$effective_sudo" | /usr/bin/grep -Eq 'NOPASSWD:|!authenticate'; then
  echo 'Remove existing NOPASSWD/!authenticate sudo grants for this account before installation.' >&2
  exit 1
fi
case "$(uname -s)" in
  Darwin) helper=/Library/PrivilegedHelperTools/cloudrig-admin;;
  Linux) helper=/usr/local/libexec/cloudrig-admin;;
  *) echo 'Only macOS and Linux are supported.' >&2; exit 1;;
esac
for path in /var/db/cloudrig /var/run/cloudrig "$(dirname "$helper")"; do
  if [ -L "$path" ]; then echo "Refusing symlink: $path" >&2; exit 1; fi
done
install -d -m 755 "$(dirname "$helper")" /var/db /var/run/cloudrig
install -d -m 700 /var/db/cloudrig
# Verify the target directory cannot be replaced through an unprivileged writable ancestor.
parent=$(dirname "$helper")
while [ "$parent" != / ]; do
  if [ "$(uname -s)" = Darwin ]; then attrs=$(stat -f '%u %Lp' "$parent"); else attrs=$(stat -c '%u %a' "$parent"); fi
  set -- $attrs
  if [ "$1" != 0 ] || [ "$((0$2 & 022))" != 0 ]; then echo "Unsafe helper parent: $parent" >&2; exit 1; fi
  parent=$(dirname "$parent")
done
install -d -m 755 /etc/sudoers.d
policy=$(mktemp /etc/sudoers.d/.cloudrig.XXXXXX)
trap 'rm -f "$policy"' EXIT HUP INT TERM
cat > "$policy" <<POLICY
# Fresh authentication for all sudo commands by this account prevents generic cached-root-shell bypass.
Defaults:"$owner_name" timestamp_timeout=0, authenticate, !setenv
Defaults!$helper timestamp_timeout=0, authenticate, !setenv
"$owner_name" ALL=(root) PASSWD: $helper login vastai, $helper login runpod, $helper rent *, $helper approve *, $helper start *, $helper destroy *, $helper self-test
POLICY
chmod 440 "$policy"
/usr/sbin/visudo -c -s -f "$policy"
install -m 755 "$source_binary" "$helper"
chown root "$helper"
printf '{"uid":%s,"gid":%s}\n' "$SUDO_UID" "$owner_gid" > /var/db/cloudrig/owner.json
chmod 600 /var/db/cloudrig/owner.json
chown -R root /var/db/cloudrig /var/run/cloudrig
mv "$policy" "/etc/sudoers.d/zzz_cloudrig_$SUDO_UID"
trap - EXIT HUP INT TERM
/usr/sbin/visudo -c
if [ "$(uname -s)" = Darwin ]; then
  plist=/Library/LaunchDaemons/io.cloudrig.admin.plist
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>io.cloudrig.admin</string>
<key>ProgramArguments</key><array><string>$helper</string><string>daemon</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
</dict></plist>
PLIST
  chown root "$plist"; chmod 644 "$plist"
  launchctl bootout system/io.cloudrig.admin 2>/dev/null || true
  launchctl bootstrap system "$plist"
else
  cat > /etc/systemd/system/cloudrig-admin.service <<UNIT
[Unit]
Description=Cloudrig protected credentials and approved SSH signer
After=network.target
[Service]
User=root
ExecStart=$helper daemon
Restart=on-failure
Environment=PATH=/usr/bin:/bin:/usr/sbin:/sbin
[Install]
WantedBy=multi-user.target
UNIT
  chmod 644 /etc/systemd/system/cloudrig-admin.service
  systemctl daemon-reload
  systemctl enable --now cloudrig-admin.service
fi
"$helper" self-test
/usr/bin/sudo -K
printf 'Installed protected helper for %s. Run crig login from your own terminal.\n' "$owner_name"
