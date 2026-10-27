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
    harness = r"""
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
        ["/bin/sh", "-c", function + "\n" + harness, "test", str(tmp_path), str(unload_polls)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert (result.returncode == 0) == succeeds, result.stderr
    assert (tmp_path / "bootstrapped").exists() == succeeds
    if not succeeds:
        assert "did not finish unloading" in result.stderr
