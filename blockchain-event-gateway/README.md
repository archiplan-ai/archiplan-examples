# Alckemy-like blokchain data provider

This project demonstrates the result of multiple design iterations made by Claude Opus 4.7 xhigh equipped with Archiplan CLI. The agent was given a short prompt stating the problem and instructed to go ahead autonomously, human only cleared session context between iterations. 

**Prompt:**
```
Design a multi-tenant, multi-region API service that gives developers JSON-RPC + WebSocket access to Ethereum mainnet, Sepolia, Bitcoin, and Cardano on-chain data.
```

As a result the agent generated detailed implementation [plan](./tasks/) using the selected stack and wrote architectural [report](./ARCHITECTURE.md) using Archiplan as primary source of truth.

Another demonstration of deep continous understanding of the system by agent equipped with Archiplan is the [report](./distributed-transactions.md) on distributed transactions, which was generated using only Archiplan project knowledge-base.