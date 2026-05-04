# tier_splitter

> Build the tier splitter:

## Properties

| Field | Value |
| --- | --- |
| Task | `tier_splitter` |
| Scope | `compliance_audit` |
| Node | `tier_splitter` |
| Node type | `Subsystem` |
| Dependencies | `2` |
| Wave | `2` |

## Architecture

```mermaid
graph LR
    admission_gate["admission_gate"]
    chain_writer["chain_writer"]
    tier_splitter(["**tier_splitter**"]):::central
    admission_gate -->|calls| tier_splitter
    tier_splitter -->|calls| chain_writer
    classDef central fill:#fde68a,stroke:#b45309,stroke-width:2px;
```

## Implementation summary

- Build the tier splitter: classifies each admitted entry into TIER-1 (per-tenant-key shredded payload) and TIER-2 (organizational long-lived structural witness) and writes both via chain_writer
- key-destroyed encrypt-failure surfaces a witness
- TIER-2 forward-secure epochs.

## Node definition (`tier_splitter` — Subsystem)

- Composes the paired Tier-1 + Tier-2 representation for each admitted entry.
- For per-tenant entries it requests Tier-1 encryption from tenant_store with the writer-encrypted-at-HLC H_w and writer-sequence S_w as part of the request
- tenant_store rejects encrypt-RPCs whose H_w is at-or-after the audit-key destruction-fence-HLC H_destroy with a typed key-destroyed-at-fence response carrying H_destroy.
- On that response, tier_splitter NEVER drops silently: it writes a typed key-destroyed-encrypt-failure entry into chain_log via chain_writer carrying ONLY the Tier-2 structural witness (event type, H_w, originating component, key-destroyed reason) with NO Tier-1 ciphertext
- cert_assembler downstream interprets these as erasure-incomplete or partial-with-witnesses signal (writer believed it had a legitimate write to make AFTER the destroy fence — its upstream drain-ack contract was violated).
- For successful encryption the per-tenant audit-key handle lookup is HLC-anchored on H_w (so a key rotated between admission and tier_splitter does not encrypt under the wrong key).
- The Tier-2 structural witness is signed under the FORWARD-SECURE Tier-2 signing key for the current epoch (see chain_log epoch model) — event type, event HLC, originating component, hash commitment to the Tier-1 payload, and for routing-identifier entry types (drain-ack-receipt, drain-fence-broadcast, drain-ack-index-snapshot, key-destroyed-encrypt-failure, retroactive-protocol-violation, violation-summary, cert-input-pin) ALSO (writer_id, tenant_id, H_a, S_a / sealing-tuple as applicable) in cleartext (these are routing identifiers that exist in numerous non-shreddable parent-scope locations
- their presence in Tier-2 does not violate crypto-shred semantics, which destroys content not existence-of-tenant).
- For non-tenant-keyed entries (operator-overrides, OOB-anchor use, anchor rotation, residency-policy events, roster mutations, schema-activation, retention-shred records) Tier-1 is empty/identity and Tier-2 carries the full structural payload directly.
- Both Tier-1 ciphertext and Tier-2 witness are wrapped into a single chain-entry record passed to chain_writer with the residency-decision-tuple (V_e, R_e) and append-class label propagated from admission_gate.

## Requirements

### `r1` — r-init-tier-witness

**Summary:** Audit material is two-tier: TIER 1 (per-tenant audit-encryption key, shredded on tenant erasure) holds the tenant-attributable payload, TIER 2 (organizational long-lived OOB-anchor-rooted key, NOT shredded) holds a structural witness (event type, HLC, originating component, hash commitment to Tier-1 payload) with no tenant-identifying material; certificate-of-deletion vouches for Tier-2 witnesses post-shred.

- Origin: `initial`
- Targets: `tier_splitter`
- Matched via: `tier_splitter`
- Verifications:
  - Test tier_splitter/tier_witness.rs asserts every entry produces both a tier-1 payload and a tier-2 structural witness.

### `r2` — r-key-destroyed-encrypt-failure-witness

**Summary:** tier_splitter encrypt-RPC failure on a destroyed audit-encryption key never silently drops: tenant_store returns a typed key-destroyed-at-fence response carrying H_destroy; tier_splitter writes a key-destroyed-encrypt-failure typed entry into chain_log via chain_writer carrying only the Tier-2 structural witness plus the destroy-fence reason; no Tier-1 ciphertext. cert_assembler interprets any such entry for a tenant as erasure-incomplete or partial-with-witnesses signal: the writer believed it had a legitimate write to make AFTER the destroy fence so its upstream drain-ack contract was violated and the cert type must reflect this.

- Origin: `stressor:1:s-key-destroy-encrypt-race`
- Targets: `tier_splitter`
- Matched via: `tier_splitter`
- Verifications:
  - Test tier_splitter/key_destroyed_encrypt_failure_witness.rs asserts when tier-1 encryption fails (key DESTROYED), an explicit witness is emitted on tier-2 (no silent drop).

### `r3` — r-tier2-forward-secure-epochs

**Summary:** Tier-2 signing uses a forward-secure key-evolution scheme rooted in the OOB-anchor key hierarchy. The active Tier-2 signing key advances one epoch per chain_log micro-batch (or per fixed bounded chain-prefix); each epoch transition appends a typed tier2-epoch-witness entry committing the new epoch verification material under OOB-anchor signature. Forward security: a compromised epoch-K key cannot mint signatures attributable to epoch K-1 or earlier. A verifier validates a Tier-2 witness against the epoch verification material committed in the epoch-witness entry, anchored through the chain to OOB-anchor signature. Compromise is handled by appending a typed tier2-key-compromise entry naming the compromised epoch and the new active epoch; cert_assembler treats Tier-2 witnesses from a compromised epoch as suspect-witness verification-mode without invalidating prior epochs.

- Origin: `stressor:1:s-tier2-key-compromise`
- Targets: `tier_splitter`
- Matched via: `tier_splitter`
- Verifications:
  - Test tier_splitter/forward_secure_epochs.rs asserts tier-2 keys are rotated per documented epoch; an old epoch's compromise does not leak future epochs.

## Outputs

| Path | Purpose |
| --- | --- |
| `crates/compliance_audit/src/tier_splitter.rs` | Splitter |

## Stack details

- Rust module 'compliance_audit::tier_splitter' computing tier-2 hash-commitment to tier-1 payload; tier-2 entries written under OOB-anchor-rooted key hierarchy with forward-secure epoch rotation

## Acceptance criteria

### r-init-tier-witness

- Test tier_splitter/tier_witness.rs asserts every entry produces both a tier-1 payload and a tier-2 structural witness.

### r-key-destroyed-encrypt-failure-witness

- Test tier_splitter/key_destroyed_encrypt_failure_witness.rs asserts when tier-1 encryption fails (key DESTROYED), an explicit witness is emitted on tier-2 (no silent drop).

### r-tier2-forward-secure-epochs

- Test tier_splitter/forward_secure_epochs.rs asserts tier-2 keys are rotated per documented epoch; an old epoch's compromise does not leak future epochs.

## Related tasks (graph neighbours)

- [admission_gate](admission_gate.md)
- [chain_writer](chain_writer.md)

---

_Source of truth: `archi plan task show tier_splitter`. Regenerate with `python3 tasks/_generate.py`._
