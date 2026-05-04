# fanout_bus

> Build the per-region in-region multiplex bus:

## Properties

| Field | Value |
| --- | --- |
| Task | `fanout_bus` |
| Scope | `/` |
| Node | `fanout_bus` |
| Node type | `Store` |
| Dependencies | `0` |
| Wave | `1` |

## Architecture

```mermaid
graph LR
    fanout["fanout"]
    fanout_bus(["**fanout_bus**"]):::central
    fanout -->|reads_from| fanout_bus
    fanout -->|writes_to| fanout_bus
    classDef central fill:#fde68a,stroke:#b45309,stroke-width:2px;
```

## Implementation summary

- Build the per-region in-region multiplex bus: Redis Streams + Postgres cursor table that decouples one-time chain-stream consumption from N subscriber-handling fanout instances
- portable cursors and chain-derived ordering.

## Node definition (`fanout_bus` — Store)

- Per-region pub/sub multiplex layer that fanout publishes each upstream chain stream to and that subscriber-handling fanout instances read from to deliver to client websockets.
- Carries its own health, capacity budget, and failover policy.
- Region-local
- cross-region delivery uses the canonical-tip + portable-cursor path on region_coordinator, not bus replication. The bus does not implement subscriber-priority semantics
- mempool fairness is enforced at fanout's delivery layer, not by privileged bus consumers

## Outputs

| Path | Purpose |
| --- | --- |
| `charts/stores/fanout-bus/` | Helm chart for fanout_bus Redis + Postgres cursors |
| `crates/fanout_bus/` | Rust crate with Bus trait, Cursor type, and per-chain stream helpers |

## Stack details

- Helm chart 'charts/stores/fanout-bus' (per-region Redis cluster sized for fanout volume + Postgres schema 'fanout' for cursor persistence)
- Rust crate 'crates/fanout_bus' exposing publish-once / consume-many semantics, portable cursor types, and chain-derived ordering invariants
- Per-chain Redis stream 'fanout:<chain>:<fork>' with consumer groups 'fanout-instances' (XADD producer, XREADGROUP consumers)

## Related tasks (graph neighbours)

- [fanout](fanout.md)

---

_Source of truth: `archi plan task show fanout_bus`. Regenerate with `python3 tasks/_generate.py`._
