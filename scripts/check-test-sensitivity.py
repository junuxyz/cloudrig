#!/usr/bin/env python3
"""Check selected regression tests against deliberate defects in disposable source copies."""

import argparse
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    (
        "approval replay accepted",
        "approval.go",
        "if e != nil || strings.TrimSpace(line) != expected {",
        "if e != nil || false && strings.TrimSpace(line) != expected {",
        "^TestEachOperationRequiresItsOwnApproval$",
    ),
    (
        "resource cancellation ignored",
        "main.go",
        "if e = s.Confirm(message); e != nil {",
        "if e = s.Confirm(message); false {",
        "^TestCancellationPreservesAuthorityAndNeverMutatesProvider$",
    ),
    (
        "ambiguous allocation guard disabled",
        "main.go",
        "if db.Pending[pending] {",
        "if false && db.Pending[pending] {",
        "^TestAmbiguousRentalSurvivesBrokerRestartAndUnrelatedRental$",
    ),
    (
        "known allocation guard disabled",
        "main.go",
        "if rec.Provider == a.Provider && rec.Name == a.Name {",
        "if false && rec.Provider == a.Provider && rec.Name == a.Name {",
        "^TestKnownAllocationCannotBeDuplicatedWhenProviderListingIsStale$",
    ),
    (
        "start price recheck disabled",
        "main.go",
        "if fresh > quoted+0.000001 {",
        "if fresh > math.MaxFloat64 {",
        "^TestStartPriceRecheckPreventsUnapprovedSpending$",
    ),
    (
        "fractional GPU counts accepted",
        "main.go",
        " || math.Trunc(count) != count",
        "",
        "^TestRunPodStartRequiresVerifiableGPUCountAndExactCloudPrice$",
    ),
    (
        "kernel peer identity ignored",
        "main.go",
        "e != nil || uid != uint32(s.Owner.UID) && uid != 0",
        "e != nil || false && uid != uint32(s.Owner.UID) && uid != 0",
        "^TestUnixAPICannotImpersonateConfiguredOwner$",
    ),
    (
        "open signer ignores revocation",
        "main.go",
        "if approvalError != nil || current.Key != rec.Key || current.Host != rec.Host "
        "|| current.Port != rec.Port || current.User != rec.User || current.HostKey != rec.HostKey {",
        "if false && (approvalError != nil || current.Key != rec.Key || current.Host != rec.Host "
        "|| current.Port != rec.Port || current.User != rec.User || current.HostKey != rec.HostKey) {",
        "^TestOpenSignerConnectionsLoseAuthorityImmediatelyOnRecordChanges$",
    ),
    (
        "private allocation GPU count changed",
        "main.go",
        '"count": o.Count',
        '"count": 1',
        "^TestSuccessfulRentalCanAuthenticateUsingOnlyPublishedPublicGrant$/runpod$",
    ),
]


def run_tests(directory, pattern=None):
    command = ["go", "test", "-count=1", "-timeout=45s"]
    if pattern:
        command += ["-run", pattern]
    command.append("./...")
    return subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "docs/test-sensitivity.json")
    args = parser.parse_args()
    baseline = run_tests(ROOT / "admin")
    if baseline.returncode:
        raise SystemExit("Baseline must pass before mutation checks:\n" + baseline.stdout + baseline.stderr)
    results = []
    for name, filename, original, replacement, pattern in CASES:
        with tempfile.TemporaryDirectory(prefix="cg-mut-", dir="/tmp") as temporary:
            directory = Path(temporary)
            for source in (ROOT / "admin").iterdir():
                if source.suffix == ".go" or source.name in {"go.mod", "go.sum"}:
                    shutil.copy2(source, directory / source.name)
            target = directory / filename
            source = target.read_text()
            if original not in source:
                raise SystemExit(f"Mutation target disappeared: {name}")
            target.write_text(source.replace(original, replacement))
            run = run_tests(directory, pattern)
            output = run.stdout + run.stderr
            detected = run.returncode != 0 and "--- FAIL:" in output and "[build failed]" not in output
            status = "detected" if detected else "survived" if run.returncode == 0 else "invalid"
            results.append({"fault": name, "test": pattern, "status": status})
            print(f"{status}: {name}", flush=True)
            if not detected:
                print(output, flush=True)
    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "baseline": "passed",
        "cases": results,
        "scope": "Selected manual mutations in temporary copies; not exhaustive mutation coverage.",
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    if any(case["status"] != "detected" for case in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
