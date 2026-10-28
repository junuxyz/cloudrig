# Reliability test plan

Scope: native credential broker, provider mutations, SSH authority, and concurrent local
state. No real provider, paid resource, root installation, or sudo-policy mutation is needed.

## Rules for accepting a test

- Run real broker operations against local HTTP/Unix servers and inspect returned data, on-disk
  recovery state, actual provider request payloads, or SSH authentication results.
- Pair refusal paths with a successful control. A test that only checks that a mock was not
  called cannot establish the boundary.
- Inject faults at a boundary, not by replacing the business operation under test. Use bounded
  waits and observable synchronization rather than timing-dependent sleeps.
- Failed attempts must preserve the last valid state or leave a durable receipt that prevents
  duplicate spending. A refusal alone is insufficient if a mutation already happened.

## Cases and failure oracles

| Boundary | Scenario | Required observable result |
| --- | --- | --- |
| Unix API | Allowed reads/approved stop, spending requests, wrong peer UID, malformed input | Reads and approved stop succeed with correct provider payloads. Other requests return errors and cannot mutate the provider. Wrong UID receives no data. |
| Rental recovery | Provider receives allocation but response fails | Exactly one allocation request. A fresh broker using the same disk state refuses a repeat. A different rental cannot erase the ambiguous receipt. |
| Known allocation | SSH-key registration fails after an allocation ID is returned; listing is stale | The returned ID is on disk before registration. Retrying the same name cannot allocate a second GPU. |
| Concurrent requests | Two broker instances rent the same name | A coordinated overlap results in exactly one allocation, including when the provider list is stale. Both calls terminate and recovery state is valid JSON. |
| Credential rotation | Provider validates/rejects a newly entered key | Rejection restores the old token and authority byte-for-byte. Success uses the new token, revokes only this provider's grants, and preserves unrelated pending requests. |
| Start pricing | Price rises, missing/invalid contract quote, exact RunPod GPU/cloud/count | Increased/unverifiable quote cannot start; an unchanged quote starts exactly the intended instance with the correct payload. |
| Delete | Provider delete fails or succeeds | Signing authority is revoked before the provider receives deletion. Failure retains a recovery record; confirmed deletion permits deliberate name reuse. |
| Provider pages | Multiple pages, repeated cursor, invalid numeric data | Exact normalized results across pages; cycles/invalid quotes fail rather than returning partial/free offers. |
| SSH signer | Approved host, different host/user, revocation while socket remains open | Real OpenSSH authenticates only to the approved destination/user. An already-open proxy refuses new requests after revocation. |
| Service lifecycle | Revoke/regrant a running signer, terminate service, corrupt its authority database | Reconciliation stops/restarts the actual signer. Graceful termination or corrupt state removes live API/signing sockets and the private agent socket. |
| Durable local state | Parallel processes update distinct preferences; atomic rename fails | All committed updates survive, files retain private permissions, and failed replacement leaves the original file readable without temporary debris. |
| Agent framing | Truncated/oversized/fragmented input | Invalid frames fail without a panic; valid fragmented frames decode exactly. Fuzzing checks length/protocol invariants. |

## Execution

1. Add the cases above and run focused tests. Repair any demonstrated production defect with a
   regression test that exercises the failure, not the patch's internal shape.
2. Run the complete Python suite and native suite with race detection and a deadline.
3. Run bounded Go fuzz campaigns for untrusted protocol inputs.
4. Record commands, failures found, and untested OS/live-provider limits
   in the verification document. Coverage is diagnostic, not a pass threshold.
