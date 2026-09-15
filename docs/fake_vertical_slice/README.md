# Fake Artifact Vertical Slice

This is a test-only implementation of one portable managed tool. It models a deterministic `fake-tool` payload and exercises the minimum protected transaction without touching the host environment.

Run from the workspace root:

```powershell
python .\cli\fake_vertical_slice\fake_vertical_slice.py validate-schemas
python .\cli\fake_vertical_slice\fake_vertical_slice.py test
```

The slice creates a temporary root containing `store`, `tools`, `exposure`, `tx` and a WAL SQLite registry. It validates the local JSON Schema set, computes a deterministic tree digest, creates a canonical plan, issues a test-only HMAC approval, and commits the following sequence:

```text
PROPOSED -> APPROVED -> FETCHED -> VERIFIED -> STAGED -> COMMITTED
  -> REGISTERED -> ACTIVE_BOUND -> EXPOSED -> VERIFIED_AGAIN -> FINALIZED
```

The test asserts:

- a replayed approval nonce is rejected;
- a source modified after planning is rejected with `DIGEST_MISMATCH`;
- interruption immediately after `ACTIVE_BOUND` is recoverable and finalizes from the journal;
- the first generation remains represented as the previous instance during an update;
- the process `PATH` is unchanged;
- the fake payload is never executed.

The fake slice does not prove Windows ACLs, UAC, named-pipe impersonation, service hardening, or production cryptographic key storage. Those belong to the Broker L2/L3 test plan.
