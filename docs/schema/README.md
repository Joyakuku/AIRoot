# AIROOT v1 Schema Catalog

These files are the versioned wire and state contracts for the first implementation slice. They use JSON Schema draft 2020-12 and keep the authority boundary explicit:

- `state/registry.db` remains the authoritative declared state;
- these schemas validate ingress, egress, journal projections and plan/approval documents;
- JSON exports are projections and are never accepted as direct database edits;
- `store` owns payloads while `tools` and `env` own bindings/views.

The schema set is intentionally split by contract so a caller can validate a single boundary without importing the whole registry model:

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
| `search-request.schema.json` | File search input |
| `search-response.schema.json` | Search envelope and freshness semantics |
| `where-response.schema.json` | Deterministic capability selection result |
| `doctor-response.schema.json` | Diagnostic result with stable remediation |
| `gc-plan.schema.json` | Payload garbage collection plan |

Compatibility rules:

1. `schema_version: 1` is the only accepted major version in this slice.
2. Adding an optional property is a minor-compatible change; changing a type, enum, required field, digest algorithm or state meaning requires a new schema id.
3. Unknown properties are rejected at every security boundary. Future versions must migrate explicitly before a broker accepts them.
4. `plan_hash` is calculated from the canonical plan with `plan_hash` omitted, using the `jcs-rfc8785-compatible` canonicalization label. The fake slice uses deterministic sorted JSON as a test substitute and records that fact in its test report.
5. `test_hmac_sha256` is permitted only in the fake slice. Production approval tokens must use `ed25519` or an equivalent protected signing mechanism.

Corrections and minor additions made while implementing P1 (see
`../AIROOT-v0.3-实现决策记录.md`, ADR-0002/ADR-0003). The set grew from 18 to **19** files in
the ADR-0004 work, and `schema_version` stays 1:

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

The validation runners are:

```powershell
python .\cli\tests\..\fake_vertical_slice\fake_vertical_slice.py validate-schemas   # meta-schema + fixtures
python -m pytest cli/tests -q                                                      # L0/L1 suite, includes schema checks
```

