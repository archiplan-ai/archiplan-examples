# schema_registry

> Build the audit schema registry:

## Properties

| Field | Value |
| --- | --- |
| Task | `schema_registry` |
| Scope | `compliance_audit` |
| Node | `schema_registry` |
| Node type | `SubStore` |
| Dependencies | `0` |
| Wave | `1` |

## Architecture

```mermaid
graph LR
    admission_gate["admission_gate"]
    schema_registry(["**schema_registry**"]):::central
    admission_gate -->|writes_to| schema_registry
    admission_gate -->|reads_from| schema_registry
    classDef central fill:#fde68a,stroke:#b45309,stroke-width:2px;
```

## Implementation summary

- Build the audit schema registry: per-(component, entry_class) schema definition with two-phase activation (PREPARE + ACTIVATE) so admission_gate can reject entries that don't conform.

## Node definition (`schema_registry` — SubStore)

- In-process registry of currently-active entry-type schemas under a TWO-PHASE PROPOSE/ACTIVATE protocol. region_coordinator compliance_audit_owner publishes a new schema version V+1 by appending a typed schema-version-PROPOSE entry into chain_log carrying the proposed activation HLC H_act in the future. admission_gate observes the PROPOSE entry, pre-loads (V, V+1) into schema_registry, and exposes the (V, V+1, H_act) triple.
- Strict cutover: admission_gate admits each entry under V before H_act and under V+1 at-or-after H_act based on the writer-encrypted-at-HLC
- never both for the same logical event.
- A subsequent typed schema-version-ACTIVATE entry post-H_act seals the transition and removes V from schema_registry once no in-flight admissions reference it.
- Replicas more than (H_act - readiness_window) behind the PROPOSE entry deny-by-default for affected entry types until they catch up.
- Sourced schema records are themselves typed entries in chain_log
- schema_registry is reconstructable by replay over those entries.

## Requirements

### `r1` — r-schema-two-phase-activation

**Summary:** Schema-version activation across admission_gate replicas is two-phase with a quorum-witnessed activation HLC H_act. PROPOSE entry from compliance_audit_owner pre-loads V+1 alongside V in schema_registry; ACTIVATE is the temporal cutover at H_act. admission_gate strictly admits entry types under V before H_act and under V+1 at-or-after H_act using the writer-encrypted-at-HLC; never both. Replicas that have not observed the PROPOSE entry by (H_act - readiness_window) deny-by-default for the affected entry types until they catch up. schema_registry exposes the (V, V+1, H_act) triple during the propose-to-activate window. Eliminates same-event cross-schema double-writes.

- Origin: `stressor:1:s-schema-replica-skew`
- Targets: `schema_registry`
- Matched via: `schema_registry`
- Verifications:
  - Test schema_registry/two_phase_activation.rs asserts PREPARE precedes ACTIVATE; entries against a PREPARED-only schema are rejected by admission_gate.

## Outputs

| Path | Purpose |
| --- | --- |
| `crates/compliance_audit/migrations/0003_schema_registry.sql` | Migration |
| `crates/compliance_audit/src/schema_registry.rs` | Two-phase activation API |

## Stack details

- Postgres schema 'audit.schema_registry' (component, entry_class, schema_version, body JSONB, phase ENUM {PREPARED, ACTIVE, RETIRED}, activated_at_hlc); 2PC guarded

## Acceptance criteria

### r-schema-two-phase-activation

- Test schema_registry/two_phase_activation.rs asserts PREPARE precedes ACTIVATE; entries against a PREPARED-only schema are rejected by admission_gate.

## Related tasks (graph neighbours)

- [admission_gate](admission_gate.md)

---

_Source of truth: `archi plan task show schema_registry`. Regenerate with `python3 tasks/_generate.py`._
