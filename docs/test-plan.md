# Reliability test plan

Test provider requests, protected credentials, SSH approval, and concurrent local state against
local fixtures. The automated suite must not rent paid resources, install a root service, or alter
sudo policy.

## Test requirements

Call the actual operation and inspect its returned data, saved records, provider request, or SSH
result. Inject faults at the external operation, not by replacing the policy under test. Use
bounded waits and explicit process coordination.

Pair refusal tests with a successful case. A mocked call that never happened does not prove that
the real operation rejects it. Check that failed requests preserve the last valid state or leave
a recovery record that prevents duplicate spending.

## Required cases

| Area | Scenario | Expected result |
| --- | --- | --- |
| Unix API | Allowed reads and stops; forbidden spending; wrong caller; malformed input | Approved operations send the expected request. Forbidden operations change nothing and return no credentials. |
| Uncertain rental | The provider receives allocation but its response fails | Only one allocation request reaches the provider. Restart and unrelated rentals preserve the pending record. |
| Known allocation | SSH registration fails and provider listings lag | The ID is saved before registration. Retrying cannot allocate a second GPU. |
| Concurrent rentals | Two helper processes request the same name | Exactly one allocation succeeds, both calls finish, and recovery state remains valid JSON. |
| Credential rotation | The provider accepts or rejects the replacement key | Rejected validation leaves the live token and records unchanged. Success revokes only that provider's grants and preserves unrelated pending requests. |
| Installation | Replacement, validation, startup, self-test, or restoration fails; installer is killed | Previous files and service state are restored. Failed restoration retains backups. A later attempt recovers saved installation state. |
| Start pricing | Price increases or GPU/count/contract data is invalid | Unverifiable or increased prices cannot start compute. The unchanged quote starts the intended instance. |
| Deletion | The provider accepts or rejects deletion | The helper revokes signing before the request. Failure retains the allocation record. Confirmed deletion permits later name reuse. |
| Recovery | Attach an uncertain rental or clear an externally deleted one | The ID/name pair and saved key match. Clearing refuses a listed instance or failed provider read. No paid mutation occurs. |
| Provider pages | Multiple pages, repeated cursors, and invalid numbers | All valid pages appear once. Cycles and invalid quotes fail without partial results. |
| SSH signer | Approved destination, wrong host/user, or changed grant | Actual OpenSSH accepts only the approved identity and destination. An open proxy refuses new requests after revocation. |
| Service shutdown | Revoke or replace grants, cancel the process, or corrupt its state | The service stops affected signers and removes live sockets on shutdown or corruption. |
| Local state | Concurrent writes and failed atomic replacement | All committed changes survive. Private permissions remain. A failed replacement preserves the old file and removes temporary files. |
| Agent messages | Fragmented, truncated, and oversized frames | Valid fragmented messages decode unchanged. Invalid messages fail without a panic. |
| Project setup | Upload, dependency installation, or GPU check fails | The binding retains its ID and completed steps. A retry does not overwrite a completed upload. |
| Terminal UI | Empty results, failed reads, invalid checkout fields, and retry | Errors remain distinct from empty results. Invalid rentals cannot leave checkout. Retry restores usable results. |

Run focused cases while editing, then the complete Python suite, Ruff, Go race tests, and `go vet`.
Use bounded fuzz runs for untrusted parsers. Record results and remaining installed-service or
live-provider checks in [verification.md](verification.md). Coverage can identify untested code;
it does not establish correctness by itself.
