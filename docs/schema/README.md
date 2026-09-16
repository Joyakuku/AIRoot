# AIROOT v1 Schema Catalog

These files are the versioned wire and state contracts for the first implementation slice. They use JSON Schema draft 2020-12 and keep the authority boundary explicit:

- `state/registry.db` remains the authoritative declared state;
- these schemas validate ingress, egress, journal projections and plan/approval documents;
- JSON exports are projections and are never accepted as direct database edits;
- `store` owns payloads while `tools` and `env` own bindings/views.

The schema set is intentionally split by contract so a caller can validate a single boundary without importing the whole registry model. It holds **20** published JSON Schema files:

| File | Boundary |
|---|---|
| `common.schema.json` | Shared identifiers, digests, source, binding, evidence and policy enums |
| `root-marker.schema.json` | Root identity and relocation guard |
| `registry-projection.schema.json` | Read-only declared registry projection |
| `managed-tool-instance.schema.json` | Immutable managed tool registry projection |
| `runtime-instance.schema.json` | Managed Python/Node/runtime registry projection |
| `desired-manifest.schema.json` | Desired state supplied by project or signed manifest |
| `plan.schema.json` | Canonical planned mutation and side effects |
| `reference-plan.schema.json` | Canonical plan for changing how an **external reference** is exposed (session activation / persisted environment). Separate from `plan.schema.json` because that one targets managed instances only |
| `approval-token.schema.json` | Protected issuer approval bound to one plan hash |
| `broker-request.schema.json` | Protected local IPC request |
| `broker-response.schema.json` | Protected local IPC response and security mode |
| `transaction.schema.json` | Journal state projection and recovery evidence |
| `extension-manifest.schema.json` | Capability Extension declaration |
| `extension-envelope.schema.json` | Common Extension response envelope |
| `search-request.schema.json` | File search input: what to match, how to match it, and which consistency the caller will accept |
| `search-response.schema.json` | Search envelope and freshness semantics |
| `where-response.schema.json` | Deterministic capability selection result |
| `doctor-response.schema.json` | Diagnostic result with stable remediation |
| `error-response.schema.json` | The document printed when a command cannot answer: one shape for every reason code |
| `gc-plan.schema.json` | Payload garbage collection plan |

**Which of these does P1 actually print?** The table above says which boundary each file guards, not
whether this build produces the document. `schema_io.validate_self` is called before any outward JSON
is printed (AGENTS.md §7), so the names passed to it *are* the printed set — ten of the twenty:
`where-response`, `doctor-response`, `registry-projection`, `search-response`, `transaction`, `plan`,
`managed-tool-instance`, `reference-plan`, `extension-envelope` and `error-response`. **Every one of
them has a golden fixture**, and guard group 32 holds those two sets to exact equality in both
directions (a printed document with no fixture has no acceptance face; a fixture for a document
nothing prints is a wish).

The other ten are not printed by this build. **Seven** of them are **used elsewhere**: `approval-token`,
`extension-manifest`, `root-marker` and `search-request` are validated on the way *in*, or written to
disk rather than printed; `broker-request` and `broker-response` are validated by P2's protocol layer
(`cli/app/airoot/broker/protocol.py`, draft §108) — the first is *built* there and self-validated
before it would be sent, the second is only ever *parsed*, because this build has no broker to answer
it; and `common` is the fragment the others `$ref`. That is a fact about this slice, not a defect list:
the schema set is the contract, and printing is one way to exercise it.

The remaining **three have no writer at all in this build** — nothing validates them and nothing `$ref`s
them — and each says why, because "published ahead of implementation" and "describes a document that
does not exist" look identical from the outside:

- `desired-manifest.schema.json` — the **manifest boundary**: provenance plus the memory policies that skip confirmation. It is **not** `state/desired.json`: that file carries the same field names but omits `source` and `policies.auto_approve`, and filling them would invent provenance and pre-empt the memory channel (ADR-0026)
- `gc-plan.schema.json` — a **batch** collection plan (`items`/`blocked_items`/`requires_approval`). This slice's collection plan is a `plan` (one payload per plan, `operation=gc_apply`), and the `operation: "gc_plan"` document `tool gc --plan` prints is a report envelope, not this schema
- `runtime-instance.schema.json` — runtime instances arrive with P5

Guard group 34 derives "in use" from the validation call sites **and** the `$ref` graph, and holds this
list to exact equality with the remainder — so a schema cannot silently join either side, and neither
can a reason go missing.

Compatibility rules:

1. `schema_version: 1` is the only accepted major version in this slice.
2. Adding an optional property is a minor-compatible change; changing a type, enum, required field, digest algorithm or state meaning requires a new schema id.
3. Unknown properties are rejected at every security boundary. Future versions must migrate explicitly before a broker accepts them.
4. `plan_hash` is calculated from the canonical plan with `plan_hash` omitted, using the `jcs-rfc8785-compatible` canonicalization label. The fake slice uses deterministic sorted JSON as a test substitute and records that fact in its test report.
5. `test_hmac_sha256` is permitted only in the fake slice. Production approval tokens must use `ed25519` or an equivalent protected signing mechanism.

**One known asymmetry, deliberately left in the schema** (draft §108): the two `allOf` branches of
`broker-request.schema.json` cover `commit_plan` and `recover_transaction` only, so a `gc_apply` request
carrying neither `plan_ref` nor `approval_ref` is schema-valid — while the in-process operation it
stands for (`caps/lifecycle.py` `apply_gc_plan`) requires a plan hash and an approval token. The
protocol layer therefore refuses that request with `INVALID_INPUT` (8) and keeps the extra requirement
in a documented table of its own, which makes it **stricter than the published schema, not different
from it**: every request it builds still validates. Adding the branch would turn an optional pair into a
required one (rule 2 asks for a new schema id) for an operation `docs/broker/` does not define yet;
when a second `gc_apply` consumer exists, that is the moment to spend the new id.

Corrections and minor additions made while implementing P1 (see
`../AIROOT-v0.3-实现决策记录.md`, ADR-0002/ADR-0003, and ADR-0034 for the P2 repair below). The set grew
from 18 to **20** files — 19 in the ADR-0004 work, and the failure document's contract in §102 — and
`schema_version` stays 1:

| Change | Kind | Why |
|---|---|---|
| `transaction.schema.json`: `ROLLED_BACK` added to the `state` enum | defect correction | `AIROOT-v0.3-三大核心契约方案.md` §14.1 makes `ROLLED_BACK` a legal state, but the enum could not express it, so a legal state was unrepresentable. No existing member changed meaning and no fixture was invalidated. |
| `common.schema.json`: new `$defs.securityMode` and `$defs.enforcement` | optional addition (rule 2) | The test plan (P-013) requires User compatibility mode answers to declare `security_mode=policy_only`, but every response schema is `additionalProperties: false`. The new fields are optional and referenced by `doctor-response`, `where-response` and `extension-envelope`. |
| Success reason code is `SUCCESS`, not `OK` | documentation deviation | `OK` is two characters and cannot satisfy the published `reason_code` pattern `^[A-Z][A-Z0-9_]{2,63}$`. See `../AIROOT-v0.3-诊断码与ReasonCode表.md`. |
| Extension manifests use `operations`, not `operation_policies` | documentation deviation | The published `extension-manifest.schema.json` requires `operations` and its `overwrite_policy` enum rejects `replace_derived_cache`; manifests written from the planning document's example are rejected by the schema. |
| `reference-plan.schema.json` added (19th file) | new boundary | `plan.schema.json` targets managed instances only (`target.kind: managed_tool|runtime`) and cannot express a mutation of an external reference, so the reference-domain plan gets its own schema id instead of widening an existing enum (rule 2). It also machine-checks the injection-type variable blacklist via `propertyNames`, and a test asserts that list stays in step with `caps/environment.py`. |
| `reference-plan.schema.json`: `operations[].target_scope` is an inline `user\|machine` enum, not `common.schema.json#/$defs/scope` | scoped consistency | `$defs.scope` describes a *binding* scope (`system/machine/session/project`) and has no spelling for a per-user **persisted** value, which is exactly what this operation writes. `exposure.scope` already uses `user\|machine`, so using `$defs.scope` here would have made one document describe the same step two different ways. The enum is not relaxed anywhere else. |
| `registry-projection.schema.json`: optional `instances[].collected_at` | optional addition (rule 2) | `gc --apply` removes a payload but keeps the instance row, because `bindings.instance_id` references it and the binding history is exactly what `retired` exists to preserve. Without this field a consumer cannot tell a deliberate collection from a payload that vanished (`PAYLOAD_MISSING`). The `lifecycle` enum is deliberately **not** extended. |
| `where-response.schema.json`: optional `candidates[].machine_discoverable` | optional addition (rule 2) | ADR-0022 makes `where` refuse to machine-discover a Zone W binding, and the machine-level slots are where that refusal happens. Without this field the response would show a healthy row with `usable: true` that is silently passed over — a reader could not tell a deliberate exclusion from a bug. `zone` is not projected on candidate rows, so the reason had nowhere to live; the top-level `zone` describes only the *selected* candidate. |
| `error-response.schema.json` added (20th file) | new boundary | The document every failure prints had **no contract at all**: `AirootError.to_envelope` was its only writer, no published schema described it, and it was therefore the one outward document `validate_self` could not check — while AGENTS.md §7 says the core self-validates before printing any outward JSON (draft §102 measured this; §101 had just added `details` to a shape nothing pinned). One schema suffices here, unlike the 29 lane reports §100 measured, because all 96 reason codes produce the same keys. `evidence` is an array of strings, the form `doctor-response` already uses for a diagnostic's evidence; the structured `{kind, detail}` object in `common.$defs.evidence` belongs to documents that report findings. |
| `broker-response.schema.json`: `security_mode` and `enforcement` now `$ref` `common.$defs.securityMode` / `$defs.enforcement` instead of restating them inline | defect correction | The two fields were hand copies of a shared definition, and one of the copies had drifted: the response schema allowed `acl_and_broker` where the definition — and the three schemas that `$ref` it, and the value this build prints from `cli.py`, `ext/envelope.py` and `caps/doctor.py` — say `acl_enforced`. No document, fixture or code path in the tree ever contained `acl_and_broker`, so the spelling was unreachable as well as contradictory. It survived because nothing read that schema until P2's protocol layer did; the fields are exactly the pair ADR-0002 created as **one** contract, so the fix is to keep one definition rather than to teach each reader two spellings. No existing member changed meaning and no fixture was invalidated (ADR-0003's precedent for repairing a transcription defect in place); a guard now holds every `security_mode`/`enforcement` declaration in the set equal to the shared values, and to each other. |

The validation runners are:

```powershell
python .\cli\tests\..\fake_vertical_slice\fake_vertical_slice.py validate-schemas   # meta-schema + fixtures
python -m pytest cli/tests -q                                                      # L0/L1 suite, includes schema checks
```

