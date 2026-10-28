#!/bin/sh
# Run this reviewed installer from your own terminal, not through an agent-owned password prompt.
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 077

restart_launchd() {
  service=$1
  plist=$2
  if launchctl print "$service" >/dev/null 2>&1; then
    launchctl bootout "$service"
    remaining=10
    while launchctl print "$service" >/dev/null 2>&1; do
      if [ "$remaining" -eq 0 ]; then
        echo "Service did not finish unloading: $service" >&2
        return 1
      fi
      sleep 1
      remaining=$((remaining - 1))
    done
  fi
  launchctl bootstrap system "$plist"
}

if [ "$(id -u)" != 0 ] || [ -z "${SUDO_UID:-}" ] || [ "$SUDO_UID" = 0 ]; then
  echo 'Run: sudo -k sh scripts/install-admin.sh dist/cloudrig-admin' >&2
  exit 1
fi
case "$SUDO_UID" in
  *[!0-9]*|'') exit 1 ;;
esac
owner_name=$(id -nu "$SUDO_UID")
owner_gid=$(id -g "$owner_name")
case "$owner_name" in
  *[!A-Za-z0-9_.-]*|'')
    echo 'Unsupported local account name.' >&2
    exit 1
    ;;
esac
source_binary=${1:-dist/cloudrig-admin}
if [ ! -f "$source_binary" ] || [ -L "$source_binary" ]; then
  echo 'Build the native helper first.' >&2
  exit 1
fi
case "$(uname -s)" in
  Darwin)
    helper=/Library/PrivilegedHelperTools/cloudrig-admin
    runtime_dir=/Library/PrivilegedHelperTools/cloudrig
    service_file=/Library/LaunchDaemons/io.cloudrig.admin.plist
    ;;
  Linux)
    helper=/usr/local/libexec/cloudrig-admin
    runtime_dir=/var/run/cloudrig
    service_file=/etc/systemd/system/cloudrig-admin.service
    ;;
  *)
    echo 'Only macOS and Linux are supported.' >&2
    exit 1
    ;;
esac
# Check existing ancestors before creating directories or changing permissions.
for path in /var/db/cloudrig "$runtime_dir" "$(dirname "$helper")" /etc/sudoers.d "$(dirname "$service_file")"; do
  if [ -L "$path" ]; then
    echo "Refusing symlink: $path" >&2
    exit 1
  fi
  parent=$path
  while [ ! -d "$parent" ]; do parent=$(dirname "$parent"); done
  parent=$(CDPATH='' cd -P -- "$parent" && pwd -P)
  while [ "$parent" != / ]; do
    if [ "$(uname -s)" = Darwin ]; then
      attrs=$(stat -f '%u %Lp' "$parent")
    else
      attrs=$(stat -c '%u %a' "$parent")
    fi
    set -- $attrs
    if [ "$1" != 0 ] || [ "$((0$2 & 022))" != 0 ]; then
      echo "Unsafe installation directory: $parent" >&2
      exit 1
    fi
    parent=$(dirname "$parent")
  done
done
install -d -m 755 "$(dirname "$helper")" "$runtime_dir" /etc/sudoers.d "$(dirname "$service_file")"
install -d -m 700 /var/db/cloudrig
policy_file="/etc/sudoers.d/zzz_cloudrig_$SUDO_UID"
owner_file=/var/db/cloudrig/owner.json
journal=/var/db/cloudrig-install-backup
install_lock=/var/db/cloudrig-install.lock
if ! mkdir "$install_lock" 2>/dev/null; then
  echo "Another installation may be running. If its process has ended, remove $install_lock and rerun this installer to restore the saved installation." >&2
  exit 1
fi
printf '%s\n' "$$" > "$install_lock/pid"
if [ -e "$journal" ] || [ -L "$journal" ]; then
  if [ -L "$journal" ] || [ ! -d "$journal" ] ||
     [ ! -f "$journal/uid" ] || [ "$(cat "$journal/uid")" != "$SUDO_UID" ]; then
    echo "Refusing a backup from another account or an incomplete backup. Inspect $journal before retrying." >&2
    rm -rf "$install_lock"
    exit 1
  fi
fi

# Snapshots contain only these four files, never provider credentials or receipts.
restore_file() {
  target=$1
  saved=$2
  if [ -f "$journal/$saved" ]; then
    cp -p "$journal/$saved" "$target.cloudrig-restore" || return 1
    mv -f "$target.cloudrig-restore" "$target" || return 1
  else
    rm -f "$target" || return 1
  fi
}

rollback() {
  if [ ! -f "$journal/prepared" ] || [ -f "$journal/committed" ]; then return 0; fi
  echo 'Restoring the previous Cloudrig installation.' >&2
  if [ -f "$journal/service-touched" ]; then
    if [ "$(uname -s)" = Darwin ]; then
      if launchctl print system/io.cloudrig.admin >/dev/null 2>&1; then
        launchctl bootout system/io.cloudrig.admin || return 1
        remaining=10
        while launchctl print system/io.cloudrig.admin >/dev/null 2>&1; do
          [ "$remaining" -gt 0 ] || return 1
          sleep 1
          remaining=$((remaining - 1))
        done
      fi
    else
      systemctl stop cloudrig-admin.service || return 1
      systemctl disable cloudrig-admin.service || return 1
    fi
  fi
  restore_file "$helper" helper || return 1
  restore_file "$policy_file" policy || return 1
  restore_file "$owner_file" owner || return 1
  restore_file "$service_file" service || return 1
  /usr/sbin/visudo -c || return 1
  if [ -f "$journal/service-touched" ]; then
    if [ "$(uname -s)" = Darwin ]; then
      if [ -f "$journal/active" ]; then
        launchctl bootstrap system "$service_file" || return 1
      fi
    else
      systemctl daemon-reload || return 1
      if [ -f "$journal/enabled" ]; then systemctl enable cloudrig-admin.service || return 1; fi
      if [ -f "$journal/active" ]; then systemctl start cloudrig-admin.service || return 1; fi
    fi
  fi
}

finish() {
  result=$?
  trap - EXIT HUP INT TERM
  if [ "$result" -ne 0 ]; then
    if ! rollback; then
      echo "Automatic restoration failed. Backups remain in $journal. Fix the reported system error and rerun this installer." >&2
      rm -rf "$install_lock"
      exit 1
    fi
  fi
  for target in "$helper" "$policy_file" "$owner_file" "$service_file"; do
    rm -f "$target.cloudrig-new" "$target.cloudrig-restore"
  done
  rm -rf "$journal" "$install_lock"
  exit "$result"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -e "$journal" ] || [ -L "$journal" ]; then
  if [ -L "$journal" ]; then echo 'Unsafe installation backup directory.' >&2; exit 1; fi
  # A previous interrupted attempt must be restored before taking new snapshots.
  rollback
  rm -rf "$journal"
fi
if [ -f "$owner_file" ] &&
   ! grep -Eq '\"uid\"[[:space:]]*:[[:space:]]*'"$SUDO_UID"'[[:space:]]*[,}]' "$owner_file"; then
  echo 'Install from the account that owns this Cloudrig installation.' >&2
  exit 1
fi
mkdir -m 700 "$journal"
printf '%s\n' "$SUDO_UID" > "$journal/uid"
for entry in helper policy owner service; do
  case "$entry" in
    helper) target=$helper ;;
    policy) target=$policy_file ;;
    owner) target=$owner_file ;;
    service) target=$service_file ;;
  esac
  if [ -L "$target" ] || { [ -e "$target" ] && [ ! -f "$target" ]; }; then
    echo "Refusing non-regular installation file: $target" >&2
    exit 1
  fi
  if [ -f "$target" ]; then cp -p "$target" "$journal/$entry"; fi
done
if [ "$(uname -s)" = Darwin ]; then
  if launchctl print system/io.cloudrig.admin >/dev/null 2>&1; then touch "$journal/active"; fi
else
  if systemctl is-active --quiet cloudrig-admin.service; then touch "$journal/active"; fi
  enabled_state=$(systemctl is-enabled cloudrig-admin.service 2>/dev/null) || true
  case "$enabled_state" in
    enabled) touch "$journal/enabled" ;;
    disabled|static|not-found) ;;
    *) echo "Unsupported existing service state: $enabled_state. Leave its custom configuration unchanged and resolve it before installing." >&2; exit 1 ;;
  esac
fi
policy="$journal/new-policy"
cat > "$policy" <<POLICY
# Cloudrig authentication settings apply only to the protected helper.
Defaults!$helper timestamp_timeout=0, authenticate, !setenv
"$owner_name" ALL=(root) PASSWD: $helper login vastai, $helper login runpod, $helper rent *, $helper approve *, $helper start *, $helper destroy *, $helper recover *, $helper self-test, $helper auth-check
POLICY
chmod 440 "$policy"
/usr/sbin/visudo -c -s -f "$policy"
# Stage and validate all replacement files before modifying the current installation.
install -m 755 "$source_binary" "$journal/new-helper"
chown root "$journal/new-helper"
printf '{"uid":%s,"gid":%s}\n' "$SUDO_UID" "$owner_gid" > "$journal/new-owner"
chmod 600 "$journal/new-owner"
if [ "$(uname -s)" = Darwin ]; then
  cat > "$journal/new-service" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>io.cloudrig.admin</string>
<key>ProgramArguments</key><array><string>$helper</string><string>daemon</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
</dict></plist>
PLIST
  plutil -lint "$journal/new-service"
else
  cat > "$journal/new-service" <<UNIT
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
fi
chmod 644 "$journal/new-service"
touch "$journal/prepared"
# Same-directory replacement avoids exposing partially copied executables or policy.
for entry in helper policy owner service; do
  case "$entry" in
    helper) target=$helper ;;
    policy) target=$policy_file ;;
    owner) target=$owner_file ;;
    service) target=$service_file ;;
  esac
  cp -p "$journal/new-$entry" "$target.cloudrig-new"
  mv -f "$target.cloudrig-new" "$target"
done
/usr/sbin/visudo -c
touch "$journal/service-touched"
if [ "$(uname -s)" = Darwin ]; then
  restart_launchd system/io.cloudrig.admin "$service_file"
else
  systemctl daemon-reload
  systemctl restart cloudrig-admin.service
fi
remaining=10
until "$helper" self-test; do
  [ "$remaining" -gt 0 ] || exit 1
  sleep 1
  remaining=$((remaining - 1))
done
if [ "$(uname -s)" = Linux ]; then systemctl enable cloudrig-admin.service; fi
touch "$journal/committed"
printf 'Installed protected helper for %s. Run crig login from your own terminal.\n' "$owner_name"
