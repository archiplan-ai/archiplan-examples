# drain_ack_index

> Build the drain-ack index:

## Properties

| Field | Value |
| --- | --- |
| Task | `drain_ack_index` |
| Scope | `compliance_audit` |
| Node | `drain_ack_index` |
| Node type | `SubStore` |
| Dependencies | `0` |
| Wave | `1` |

## Architecture

```mermaid
graph LR
    admission_gate["admission_gate"]
    drain_ack_index(["**drain_ack_index**"]):::central
    admission_gate -->|writes_to| drain_ack_index
    admission_gate -->|reads_from| drain_ack_index
    classDef central fill:#fde68a,stroke:#b45309,stroke-width:2px;
```

## Implementation summary

- Build the drain-ack index: lookup table for per-(tenant, drain_id) ack records consumed by admission_gate to gate late-write rejection.

## Node definition (`drain_ack_index` — SubStore)

- In-memory authoritative index of per-(writer, tenant) drain-ack state plus per-tenant fence state plus per-(writer, tenant) violation-coalescing state.
- For each (writer_id, tenant_id) pair: (sealing-HLC H_a, sealing-sequence S_a, originating drain-fence-broadcast id, chain-entry sequence at which the ack was attested in chain_log) plus violation-counter and sliding-window state for late-write coalescing.
- For each tenant_id: tenant-state flag ∈ {UNFENCED, FENCED} (flips UNFENCED → FENCED on the first admitted drain-fence-broadcast entry
- never flips back) so admission_gate cold-miss semantics are deterministic.
- Updated only by admission_gate as a strictly-after-chain-append side effect of admitting drain-ack-receipt, drain-fence-broadcast, protocol-violation, violation-summary, or drain-ack-index-snapshot entries.
- Rebuildable on restart from the most recent drain-ack-index-snapshot entry in chain_log plus forward Tier-2 replay (writer_id and tenant_id are present in cleartext on Tier-2 of the relevant entry types — see chain_log) so admission_gate cannot resume in UNFENCED-by-default for a previously FENCED tenant.

## Outputs

| Path | Purpose |
| --- | --- |
| `crates/compliance_audit/migrations/0002_drain_ack_index.sql` | Migration |
| `crates/compliance_audit/src/drain_ack_index.rs` | UPSERT API + lookup by (tenant_id, drain_id) |

## Stack details

- Postgres schema 'audit.drain_ack_index' (tenant_id, drain_id, component_id, ack_status, hlc) — UPSERT-only

## Related tasks (graph neighbours)

- [admission_gate](admission_gate.md)

---

_Source of truth: `archi plan task show drain_ack_index`. Regenerate with `python3 tasks/_generate.py`._
