#!/bin/sh
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH

if [ "$(id -u)" != 0 ] || [ -z "${SUDO_UID:-}" ] || [ "$SUDO_UID" = 0 ]; then
  echo 'Run: sudo -k /bin/sh scripts/uninstall-admin.sh' >&2
  exit 1
fi
case "$SUDO_UID" in
  *[!0-9]*|'') exit 1 ;;
esac
if [ -f /var/db/cloudrig/owner.json ] &&
   ! grep -Eq '"uid"[[:space:]]*:[[:space:]]*'"$SUDO_UID"'[[:space:]]*[,}]' /var/db/cloudrig/owner.json; then
  echo 'Uninstall from the account that owns this Cloudrig installation.' >&2
  exit 1
fi

case "$(uname -s)" in
  Darwin)
    if launchctl print system/io.cloudrig.admin >/dev/null 2>&1; then
      launchctl bootout system/io.cloudrig.admin
    fi
    rm -f /Library/LaunchDaemons/io.cloudrig.admin.plist
    rm -f /Library/PrivilegedHelperTools/cloudrig-admin
    ;;
  Linux)
    if [ -f /etc/systemd/system/cloudrig-admin.service ]; then
      systemctl disable --now cloudrig-admin.service
      rm -f /etc/systemd/system/cloudrig-admin.service
      systemctl daemon-reload
    fi
    rm -f /usr/local/libexec/cloudrig-admin
    ;;
  *)
    echo 'Only macOS and Linux are supported.' >&2
    exit 1
    ;;
esac

rm -f "/etc/sudoers.d/zzz_cloudrig_$SUDO_UID"
/usr/sbin/visudo -c
/usr/bin/sudo -K
echo 'Removed the service, helper, and Cloudrig sudo policy.'
echo 'Provider instances are unchanged. Check your provider account to stop billing.'
echo 'Protected credentials and receipts remain in /var/db/cloudrig for recovery or reinstallation.'
echo 'Local SSH aliases remain in ~/.ssh/cloudrig; they no longer have an active signer.'
