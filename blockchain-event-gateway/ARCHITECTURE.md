# Architecture overview — Blockchain Event Gateway

> A multi-tenant, multi-region API service that gives developers JSON-RPC + WebSocket access to Ethereum mainnet, Sepolia, Bitcoin, and Cardano on-chain data — current and historical — with reorg-safe semantics, per-key rate limits, residency-pinned tenant data, and crypto-shred compliance audit.

---

## At a glance

| | |
| --- | --- |
| Problem | Programmatic read access to four chains (ETH/Sepolia/BTC/ADA) over each chain's native JSON-RPC + WS surface, multi-tenant, reorg-safe |
| Spec version | `v10` (hardened) |
| Plan | `gateway-v1` — 71 implementation tasks across 10 dependency waves |
| Top-level nodes | **17** — 4 ChainPools, 6 Stores, 6 Services, 1 Client |
| Edges (root) | **39** — calls / reads_from / writes_to / proxies_to / connects_to |
| Languages | Rust everywhere (`tokio` + `axum` + `tonic` + `jsonrpsee`) |
| Substrate | Kubernetes 1.30+, **one cluster per region** |

---

## What it does, in one picture

```mermaid
graph LR
    user(["Developer<br/>(API key)"]) -->|JSON-RPC + WS| edge
    edge -->|edge-attested| gateway

    gateway -->|RPC| chain_router
    gateway -->|subscribe| fanout
    gateway -->|metrics| usage_meter
    gateway -.->|auth| auth_cache

    chain_router -->|proxies| pools[("Chain pools<br/>ETH / Sepolia<br/>BTC / Cardano")]
    fanout -->|proxies| pools
    fanout -.->|cursors| fanout_bus
    fanout -.->|index| address_index

    usage_meter -->|aggregate| metrics_store

    rc["region_coordinator<br/>(global control plane)"] -.->|policy / leases / flags| gateway
    rc -.->|tip quorum / lifecycle| chain_router
    rc -.->|tip / lifecycle| fanout
    rc -->|writes| compliance_audit

    tenant_store -->|cross-region replicas| tenant_store

    classDef chain fill:#fef3c7,stroke:#92400e;
    classDef store fill:#e0e7ff,stroke:#3730a3;
    classDef ctrl fill:#fde68a,stroke:#b45309,stroke-width:2px;
    class pools chain;
    class auth_cache,fanout_bus,address_index,metrics_store,compliance_audit,tenant_store store;
    class rc ctrl;
```

The four major flows (request, subscribe, coordinate, audit) all share the same auth path and the same compliance trail.

---

## Component map

### Tiers

```mermaid
graph TB
    subgraph Edge["Edge tier (residency-aware anycast)"]
        edge["edge<br/>TLS, WAF, edge-attestation"]
    end

    subgraph Data["Data plane (per region)"]
        direction LR
        gateway["gateway<br/>auth · rate limit · dispatch"]
        chain_router["chain_router<br/>per-(chain,fork) routing"]
        fanout["fanout<br/>WS multiplex"]
        usage_meter["usage_meter<br/>cost driver"]
    end

    subgraph Control["Control plane (M-of-regions)"]
        region_coordinator["region_coordinator<br/>HLC · leases · residency · roster · health"]
    end

    subgraph Stores["Per-region stores"]
        direction LR
        tenant_store["tenant_store<br/>(canonical, replicated)"]
        auth_cache["auth_cache<br/>(Redis)"]
        metrics_store["metrics_store<br/>(Timescale)"]
        fanout_bus["fanout_bus"]
        address_index["address_index"]
        compliance_audit["compliance_audit<br/>(hash-chained)"]
    end

    subgraph Chains["Chain pools (per region, mTLS)"]
        direction LR
        eth_archive["eth_archive (Erigon)"]
        sepolia_archive["sepolia_archive (Erigon)"]
        btc_archive["btc_archive (Bitcoin Core + ZMQ)"]
        ada_archive["ada_archive (cardano-node + Ogmios)"]
    end

    Edge --> Data
    Data --> Stores
    Data --> Chains
    Control -.->|push/ack| Data
    Control --> Stores
    Data --> compliance_audit
    Control --> compliance_audit
```

### Each box, in one line

| Component | Type | Role |
| --- | --- | --- |
| `edge` | Service | Anycast TLS terminator; signs **edge-attestation** every gateway request must carry |
| `gateway` | Service | Authenticates, rate-limits, dispatches RPC vs WS vs metrics; emits cost signals |
| `chain_router` | Service | Per-`(chain, fork)` routing across replicas; canonicalizes, quarantines, drains |
| `fanout` | Service | Consumes chain head/mempool **once per region**, multiplexes to N WS subscribers |
| `usage_meter` | Service | Per-tenant cost aggregation; cross-region delta reporting |
| `region_coordinator` | Service | Global control plane on **openraft** quorum across regions |
| `tenant_store` | Store | Canonical tenant + key + plan record; CAS-on-`(record_version, lease_id)` |
| `auth_cache` | Store | Redis credential / rate-limit cache; HLC-bounded pending markers |
| `metrics_store` | Store | TimescaleDB; ≥30d usage / error / latency / headroom |
| `fanout_bus` | Store | Redis Streams + Postgres cursors; in-region multiplex bus |
| `address_index` | Store | Per-chain `address → event` index for sub-linear address-watch matching |
| `compliance_audit` | Store | Append-only **hash-chained** Postgres (Tier-1) + S3 Object Lock (Tier-2) |
| `eth_archive` / `sepolia_archive` | ChainPool | Erigon archive + pruned tier StatefulSets |
| `btc_archive` | ChainPool | Bitcoin Core with `txindex` + ZMQ rawmempool publisher |
| `ada_archive` | ChainPool | cardano-node + Ogmios bridge (chain-sync / state-query / tx-monitor) |
| `client` | Client | Rust SDK on crates.io: JSON-RPC + WS with reconnect/cursor resumption |

---

## How a JSON-RPC request flows

```mermaid
sequenceDiagram
    autonumber
    participant C as client
    participant E as edge
    participant G as gateway
    participant AC as auth_cache
    participant TS as tenant_store
    participant CR as chain_router
    participant P as ChainPool
    participant UM as usage_meter
    participant CA as compliance_audit

    C->>E: HTTPS JSON-RPC + API key
    E->>E: WAF, IP rate limit, sign edge-attestation
    E->>G: forward (with edge-attestation)
    G->>AC: lookup (key, residency, throttle)
    alt cache miss
        AC-->>G: miss → cold path
        G->>TS: hydrate canonical record
        TS-->>G: record + lease_id + plan_version
    end
    G->>G: HLC-bucketed rate limit + cost class
    G->>CR: forward verbatim (passthrough)
    CR->>CR: pick (chain, fork) sub-pool · drop quarantined
    CR->>P: JSON-RPC over mTLS
    P-->>CR: response bytes
    CR->>CR: canonicalize · attach finality tag
    CR-->>G: canonicalized response
    G-->>E: response (re-evaluate throttle on chunks)
    E-->>C: response
    par fire-and-forget
        G->>UM: cost signal (tenant, cost class)
        UM->>CA: append cost-event audit entry
    end
```

Two things to notice:

- **Edge-attestation is mandatory.** Gateway rejects any request that doesn't carry one — there is no implicit network-position trust.
- **Hot path never blocks.** Cache miss spawns a cold-path hydration; the request either rejects with cache-miss or continues on a documented optimistic path.

---

## How a WebSocket subscription flows

```mermaid
sequenceDiagram
    autonumber
    participant C as client
    participant E as edge
    participant G as gateway
    participant F as fanout
    participant FB as fanout_bus
    participant AI as address_index
    participant P as ChainPool
    participant RC as region_coordinator
    participant CA as compliance_audit

    C->>E: WSS + API key
    E->>G: forward (subscription_path)
    G->>G: auth_check · register intent
    G->>F: subscribe (chain, filter)
    F->>FB: open per-cursor stream
    F->>P: chain-sync / head / mempool (one consumer per region)
    P-->>F: head event / pending tx
    alt address watch
        F->>AI: index(address → event)
    end
    F-->>G: event with finality tag
    G-->>C: WS frame

    Note over RC,F: rollback / fork transition
    RC->>F: canonical-tip update (per chain, fork)
    F-->>G: rollback notification + rebind
    G-->>C: rollback frame
    F->>CA: append fork-transition audit entry
```

Why fanout sits behind `fanout_bus`: chain-streams are consumed **once per region** (head + mempool). The bus then multiplexes to N gateway-side subscriber instances using portable cursors with chain-derived ordering, so reconnect storms don't fan out into the chain pool.

---

## The control plane (`region_coordinator`)

`region_coordinator` is the only component that runs as a true cross-region cluster — an `openraft` quorum with one replica per region, talking over `tonic` + `rustls` mTLS. Every per-region data-plane service treats it as the source of truth for global facts.

```mermaid
graph LR
    subgraph rc["region_coordinator (openraft, M-of-regions)"]
        direction TB
        qc["quorum_core"]
        subgraph lanes["6 Raft lanes"]
            tip_lane
            tombstone_lane
            aggregate_lane
            control_lane
            lease_lane
            health_lane
        end
        subgraph subs["13 subservices"]
            hlc_service
            tip_quorum
            lifecycle_gate
            residency_publisher
            quota_aggregator
            flag_propagator
            credential_roster
            cert_bootstrap
            gateway_health_surface
            compliance_audit_owner
            lease_issuer
            offboarding_orchestrator
        end
    end

    chain_router -->|head observations| tip_quorum
    tip_quorum --> fanout
    residency_publisher -->|push+ack| gateway
    residency_publisher --> edge
    residency_publisher --> usage_meter
    flag_propagator --> auth_cache
    lease_issuer --> tenant_store
    lifecycle_gate -->|drain-fence| chain_router
    lifecycle_gate -->|drain-fence| fanout
    lifecycle_gate -->|drain-fence| gateway
    cert_bootstrap -.->|OOB hardware anchor| custodians[("M-of-N custodians<br/>(cloud KMS)")]
    rc --> compliance_audit
```

Each lane has a distinct retention class so high-volume tombstones can't starve low-volume control messages. Residency policy activation is a **2PC**: PREPARE requires a quarantine-and-relocate ack from `tenant_store`, then COMMIT activates `V+1` region-wide, with explicit ABORT semantics.

### Three governance properties worth knowing

1. **Single-writer-per-tenant** is enforced by `lease_issuer`: every tenant has an HLC-bounded lease token, and `tenant_store` writes are CAS on `(record_version, lease_id)`.
2. **M-of-N operator credentials** govern roster mutations, override admissions, and emergency anchor use. The roster itself is published through a 2PC with monotonic `roster_version`.
3. **Emergency cert recovery** is rooted in an out-of-band hardware anchor — cloud KMS keys held by geographically and organizationally distinct custodians — distinct from the day-to-day mTLS chain.

---

## The compliance audit

Every operational write plane writes directly to `compliance_audit` over an authenticated append-only RPC — never through a shared operational data path that an operational compromise could rewrite.

```mermaid
graph LR
    region_coordinator -->|writes| ca
    chain_router -->|writes| ca
    gateway -->|writes| ca
    fanout -->|writes| ca
    usage_meter -->|writes| ca
    address_index -->|writes| ca
    tenant_store -->|writes| ca

    subgraph ca["compliance_audit"]
        direction TB
        ag["admission_gate<br/>(rejects late writes)"]
        sr["schema_registry"]
        ts["tier_splitter"]
        cw["chain_writer"]
        cl[("chain_log<br/>hash-chained Postgres")]
        re["retention_enforcer"]
        ck["cert_assembler"]
        ag --> sr
        ag --> ts
        ts --> cw
        cw --> cl
        re --> cl
        ck --> cl
    end

    cl -->|Tier-2 witness| s3[("S3 Object Lock<br/>(per-region)")]
```

### Two-tier audit material

| Tier | What's in it | Key | Erasure behaviour |
| --- | --- | --- | --- |
| **Tier 1** | Full audit payload incl. tenant-identifying fields | Per-tenant audit-encryption key | **Crypto-shred** on tenant erasure (key destroyed) |
| **Tier 2** | Structural witness: event type, HLC, originating component, hash commitment to Tier-1 | Organizational long-lived (under OOB anchor) | **Never erased** |

After tenant erasure, the hash chain is intact, Tier-1 entries are unreadable, and a regulator can still verify _structure-and-presence_ of every event from Tier-2. The **certificate-of-deletion** is a typed entry (`full-ack` / `partial-with-witnesses` / `erasure-incomplete`) that vouches for this.

---

## Multi-region shape

```mermaid
graph TB
    subgraph anycast["Global anycast"]
        edge_global["edge (anycast)"]
    end

    subgraph rA["Region A"]
        gA[gateway]
        crA[chain_router]
        fA[fanout]
        umA[usage_meter]
        rcA[region_coordinator A]
        poolsA[(chain pools)]
        storesA[("auth_cache<br/>tenant_store(A)<br/>compliance_audit(A)<br/>metrics_store(A)")]
    end

    subgraph rB["Region B"]
        gB[gateway]
        crB[chain_router]
        fB[fanout]
        umB[usage_meter]
        rcB[region_coordinator B]
        poolsB[(chain pools)]
        storesB[("auth_cache<br/>tenant_store(B)<br/>compliance_audit(B)<br/>metrics_store(B)")]
    end

    subgraph rC["Region C"]
        gC[gateway]
        crC[chain_router]
        fC[fanout]
        umC[usage_meter]
        rcC[region_coordinator C]
        poolsC[(chain pools)]
        storesC[("auth_cache<br/>tenant_store(C)<br/>compliance_audit(C)<br/>metrics_store(C)")]
    end

    edge_global --> gA
    edge_global --> gB
    edge_global --> gC

    rcA <-->|openraft mTLS| rcB
    rcB <-->|openraft mTLS| rcC
    rcA <-->|openraft mTLS| rcC

    storesA -.->|logical replication| storesB
    storesB -.->|logical replication| storesC
```

- **Stateless services** (`gateway`, `edge`, `chain_router`, `fanout`, `usage_meter`) run per region and scale horizontally.
- **`region_coordinator`** is one cluster spanning regions — only place where Raft crosses region boundaries.
- **`tenant_store`** is canonical per region with logical replication for cross-region reads. Tenant data is **residency-pinned**: a tenant's records live in their assigned region(s) and writes are gated by the active `policy_version`.
- **`compliance_audit`** has per-region buckets and DBs; Tier-2 witnesses are also replicated to per-region S3 with Object Lock in compliance mode.

---

## Cross-cutting invariants worth internalizing

1. **Residency is a 2PC with cold-start pre-warm.** Every consumer (`edge`, `gateway`, `usage_meter`, `metrics_store`) must ack readiness before `residency_publisher` activates a tightening change. On cold-start, consumers pre-warm hydrate the active version inline before serving residency-pinned traffic.
2. **HLC everywhere.** Every operation is HLC-stamped. Skew above threshold → documented degraded mode. Lease TTLs, audit-key destruction fences, drain-fence ack windows are all clock-skew-bounded.
3. **Drain-fence-then-teardown.** When a tenant is offboarding **and** a node is being torn down, drain-fence flush comes first; the node either finishes its flush or writes a durable handoff record naming a successor. A drain-ack is never retracted once emitted.
4. **No resurrection.** After an erasure tombstone for tenant `T` at HLC `T_e`, any cascade with HLC > `T_e` is rejected with a documented reason. Late cascades with HLC < `T_e` apply only to a frozen audit-projection.
5. **Forks are explicit.** `chain_router` partitions every pool by `(chain, fork)` sub-pool. A fork transition is a handshake-ack between `region_coordinator`'s tip quorum, `chain_router`, and `fanout` — durable, with monotonic per-cursor ordering across rollback-then-forward-progress.

---

## Technology stack

| Concern | Tech |
| --- | --- |
| Service runtime | Rust + tokio + axum + tonic + jsonrpsee + tokio-tungstenite |
| OLTP | PostgreSQL 16 per region; sqlx + refinery; logical replication |
| Cache | Redis 7 cluster per region (TTL + Streams) |
| Audit | Postgres hash-chain (Tier-1) + S3 Object Lock compliance mode (Tier-2) |
| Time-series | Timescale extension on Postgres |
| Cross-region consensus | openraft + tonic + rustls mTLS |
| Substrate | Kubernetes 1.30+, **one cluster per region**, Helm charts per Service |
| Workload identity | SPIRE/SPIFFE; cert-manager |
| Secrets / PKI | cert-manager + cloud KMS; OOB anchor held by M-of-N custodians |
| Observability | OpenTelemetry → Prometheus + Grafana + Tempo + Loki per region |
| Chain nodes | Erigon (ETH/Sepolia) · Bitcoin Core + ZMQ (BTC) · cardano-node + Ogmios (ADA) |
| Tests | `cargo test` + `insta` (unit) · `testcontainers-rs` + `wiremock-rs` (integration) · `criterion` (perf) |
| Client SDK | `chain-gateway-client` Rust crate on crates.io |

---

## Build order (dependency waves)

The `gateway-v1` plan organizes 71 implementation tasks into **10 dependency waves**. Bottom of the stack is built first; integration tasks gate each service.

```mermaid
graph LR
    W1["Wave 1<br/>30 tasks<br/>(chain pools, stores,<br/>raft lanes, leaf services)"]
    W2["Wave 2<br/>15 tasks<br/>(per-service engines)"]
    W3["Wave 3<br/>12 tasks<br/>(coordinators,<br/>chain_router integration)"]
    W4["Wave 4<br/>3 tasks<br/>(compliance_audit,<br/>tenant_store integration)"]
    W5["Wave 5<br/>2 tasks<br/>(region_coordinator<br/>integration, auth_check)"]
    W6["Wave 6<br/>3 tasks<br/>(usage_meter, fanout, listener)"]
    W7["Wave 7<br/>3 tasks<br/>(gateway subroles)"]
    W8["Wave 8<br/>1 task<br/>(gateway integration)"]
    W9["Wave 9<br/>1 task (edge)"]
    W10["Wave 10<br/>1 task (client)"]

    W1 --> W2 --> W3 --> W4 --> W5 --> W6 --> W7 --> W8 --> W9 --> W10
```

---

## Where to look next

- [`tasks/README.md`](tasks/README.md) — full task tree + wave breakdown, every task linked
- [`tasks/<service>/README.md`](tasks/) — per-service integration task with internal architecture diagram and child tasks
- `archi plan show` — live plan summary
- `archi plan task show <id>` — canonical source for any single task
- `archi query visualize --layer epistatic` — full graph as Mermaid

---

_Generated from the `archi` spec at `gateway-v1` / `v10`. Diagrams are Mermaid; render in any GitHub-flavored Markdown viewer._
