import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("unload_polls, succeeds", [(2, True), (100, False)])
def test_launchd_restart_waits_for_unload_and_has_a_deadline(tmp_path, unload_polls, succeeds):
    installer = Path(__file__).resolve().parents[1] / "scripts" / "install-admin.sh"
    source = installer.read_text()
    start = source.index("restart_launchd() {")
    function = source[start : source.index("\n}\n", start) + 2]
    # Model launchd's asynchronous bootout. The real restart function must handle it.
    scenario = r"""
    set -eu
    cd "$1"
    polls=$2
    launchctl() {
      case "$1" in
        print)
          if [ -f unloading ]; then
            polls=$((polls - 1))
            [ "$polls" -gt 0 ] || return 1
          fi;;
        bootout) touch unloading;;
        bootstrap)
          if [ ! -f unloading ] || [ "$polls" -gt 0 ]; then
            echo 'bootstrap raced with unloading' >&2
            return 5
          fi
          touch bootstrapped;;
      esac
    }
    sleep() { :; }
    restart_launchd system/io.cloudrig.admin fixture.plist
    """
    result = subprocess.run(
        ["/bin/sh", "-c", function + "\n" + scenario, "test", str(tmp_path), str(unload_polls)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert (result.returncode == 0) == succeeds, result.stderr
    assert (tmp_path / "bootstrapped").exists() == succeeds
    if not succeeds:
        assert "did not finish unloading" in result.stderr


def run_installer_fixture(tmp_path, platform, failure, existing=True):
    """Execute the full installer against temporary paths and simulated system services."""
    import os

    root = tmp_path / "system"
    root.mkdir()
    source = (Path(__file__).resolve().parents[1] / "scripts/install-admin.sh").read_text()
    import re

    source = re.sub(r"/(?:Library|var|etc|usr/local)(?=/)", lambda m: str(root) + m[0], source)
    source = source.replace("/usr/sbin/visudo", "visudo")
    helper = root / (
        "Library/PrivilegedHelperTools/cloudrig-admin"
        if platform == "Darwin"
        else "usr/local/libexec/cloudrig-admin"
    )
    service = root / (
        "Library/LaunchDaemons/io.cloudrig.admin.plist"
        if platform == "Darwin"
        else "etc/systemd/system/cloudrig-admin.service"
    )
    policy = root / "etc/sudoers.d/zzz_cloudrig_1000"
    owner = root / "var/db/cloudrig/owner.json"
    credentials = owner.parent / "vastai.token"
    for path in (helper, service, policy, owner):
        path.parent.mkdir(parents=True, exist_ok=True)
    credentials.write_text("keep-protected-credential")
    paths = (helper, service, policy, owner)
    if existing:
        for path in paths:
            path.write_text('{"uid":1000,"gid":1000}\n' if path == owner else "previous " + path.name)
            path.chmod(0o600 if path == owner else 0o644)
        (root / "active").touch()
        (root / "enabled").touch()
    original = {path: (path.read_bytes(), path.stat().st_mode & 0o777) for path in paths if path.exists()}
    binary = tmp_path / "new-helper"
    binary.write_text(
        '#!/bin/sh\n[ "$1" = self-test ] || exit 8\n'
        + ("exit 1\n" if failure in {"self-test", "restore"} else "exit 0\n")
    )
    binary.chmod(0o755)
    mocks = r"""
    uname() { echo "$fixture_platform"; }
    id() { case "$1" in -u) echo 0;; -nu) echo tester;; -g) echo 1000;; esac; }
    chown() { :; }
    stat() { echo '0 755'; }
    sleep() { :; }
    plutil() { :; }
    fail_once() {
      if [ "$fixture_failure" = "$1" ] && [ ! -f "$fixture_root/failed" ]; then
        touch "$fixture_root/failed"
        return 1
      fi
    }
    visudo() { if [ "$#" -eq 1 ]; then fail_once policy-validation || return 1; fi; }
    mv() {
      if [ "$1" = -f ]; then shift; fi
      case "$1" in
        *cloudrig-new)
          case "$2" in
            *cloudrig-admin) step=replace-helper;;
            *zzz_cloudrig_1000) step=replace-policy;;
            *owner.json) step=replace-owner;;
            *) step=replace-service;;
          esac
          fail_once "$step" || return 1
          if [ "$fixture_failure" = killed ] && [ "$step" = replace-owner ]; then kill -KILL $$; fi;;
      esac
      command mv "$@"
    }
    systemctl() {
      case "$1" in
        is-active) test -f "$fixture_root/active";;
        is-enabled) if [ -f "$fixture_root/enabled" ]; then echo enabled; else echo disabled; return 1; fi;;
        stop) rm -f "$fixture_root/active";;
        disable) rm -f "$fixture_root/enabled";;
        start) [ "$fixture_failure" != restore ] || return 1; touch "$fixture_root/active";;
        restart) fail_once service-start || return 1; touch "$fixture_root/active";;
        enable) fail_once service-enable || return 1; touch "$fixture_root/enabled";;
        daemon-reload) fail_once service-reload;;
      esac
    }
    launchctl() {
      case "$1" in
        print) test -f "$fixture_root/active";;
        bootout) rm -f "$fixture_root/active";;
        bootstrap)
          [ "$fixture_failure" != restore ] || return 1
          fail_once service-start || return 1; touch "$fixture_root/active";;
      esac
    }
    """
    source = source.replace("export PATH\n", "export PATH\n" + mocks)
    installer = tmp_path / "installer.sh"
    installer.write_text(source)
    env = dict(
        os.environ,
        SUDO_UID="1000",
        fixture_platform=platform,
        fixture_failure=failure,
        fixture_root=str(root),
    )
    result = subprocess.run(
        ["/bin/sh", str(installer), str(binary)], env=env, text=True, capture_output=True, timeout=10
    )
    return result, root, paths, original, installer, binary, env


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
@pytest.mark.parametrize("existing", [True, False])
@pytest.mark.parametrize(
    "failure",
    [
        "replace-helper",
        "replace-policy",
        "replace-owner",
        "replace-service",
        "policy-validation",
        "service-start",
        "self-test",
    ],
)
def test_failed_installation_restores_files_and_service(tmp_path, platform, existing, failure):
    result, root, paths, original, *_ = run_installer_fixture(tmp_path, platform, failure, existing)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Automatic restoration failed" not in result.stderr, result.stderr
    for path in paths:
        if path in original:
            assert (path.read_bytes(), path.stat().st_mode & 0o777) == original[path]
        else:
            assert not path.exists(), path
    assert (root / "active").exists() == existing
    assert (root / "enabled").exists() == existing
    assert (root / "var/db/cloudrig/vastai.token").read_text() == "keep-protected-credential"
    assert not (root / "var/db/cloudrig-install-backup").exists()
    assert not (root / "var/db/cloudrig-install.lock").exists()


@pytest.mark.parametrize("failure", ["service-enable", "service-reload"])
def test_linux_service_configuration_failure_restores_old_installation(tmp_path, failure):
    result, root, paths, original, *_ = run_installer_fixture(tmp_path, "Linux", failure)
    assert result.returncode != 0
    assert "Automatic restoration failed" not in result.stderr, result.stderr
    assert all(path.read_bytes() == original[path][0] for path in paths)
    assert (root / "active").exists()
    assert (root / "enabled").exists()


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_successful_installation_limits_sudo_settings_to_helper(tmp_path, platform):
    result, root, paths, *_ = run_installer_fixture(tmp_path, platform, "")
    assert result.returncode == 0, result.stdout + result.stderr
    helper, service, policy, owner = paths
    assert helper.stat().st_mode & 0o777 == 0o755
    assert policy.stat().st_mode & 0o777 == 0o440
    assert owner.stat().st_mode & 0o777 == 0o600
    defaults = [line for line in policy.read_text().splitlines() if line.startswith("Defaults")]
    assert defaults == [f"Defaults!{helper} timestamp_timeout=0, authenticate, !setenv"]
    assert not (root / "var/db/cloudrig-install-backup").exists()
    assert (root / "active").exists()


def test_killed_installer_retains_backups_and_next_attempt_restores_them(tmp_path):
    result, root, paths, original, installer, binary, env = run_installer_fixture(tmp_path, "Linux", "killed")
    assert result.returncode == -9
    journal = root / "var/db/cloudrig-install-backup"
    assert (journal / "prepared").exists()
    # A killed process cannot release its lock. An operator checks its recorded PID first.
    import shutil

    shutil.rmtree(root / "var/db/cloudrig-install.lock")
    env["fixture_failure"] = "self-test"
    binary.write_text("#!/bin/sh\nexit 1\n")
    result = subprocess.run(
        ["/bin/sh", str(installer), str(binary)], env=env, text=True, capture_output=True, timeout=10
    )
    assert result.returncode != 0
    assert "Automatic restoration failed" not in result.stderr, result.stderr
    assert all(path.read_bytes() == original[path][0] for path in paths)
    assert not journal.exists()


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_failed_restoration_retains_backups_for_a_later_attempt(tmp_path, platform):
    result, root, paths, original, installer, binary, env = run_installer_fixture(
        tmp_path, platform, "restore"
    )
    assert result.returncode != 0
    assert "Automatic restoration failed" in result.stderr
    journal = root / "var/db/cloudrig-install-backup"
    assert (journal / "prepared").exists()
    for name, path in zip(("helper", "service", "policy", "owner"), paths, strict=True):
        assert (journal / name).read_bytes() == original[path][0]
    env["fixture_failure"] = ""
    binary.write_text("#!/bin/sh\nexit 0\n")
    result = subprocess.run(
        ["/bin/sh", str(installer), str(binary)], env=env, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert "Restoring the previous" in result.stderr
    assert not journal.exists()
    assert (root / "active").exists()
