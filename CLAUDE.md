# Ezekiel — the mission

**Find this trader's other wallets, and catch him the moment he moves to a new one.**

Target: `0x45d26f28196d226497130c4bac709d808fed4029` (Hyperliquid, possibly GCR).
The owner copy-trades him manually. If he migrates undetected, the owner is
following a dead wallet and the project has failed at the only thing it does.

He almost certainly has other wallets already. Assume so, and hunt accordingly.

Everything else in this repo — the graph, the scanner, the correlator, the
accounting — exists only to serve that. When a design choice is unclear, pick the
one that makes an undetected migration less likely.

---

## The standing instruction

Use **everything**: tracing, scanning, pattern recognition, behavioural
fingerprinting, on-chain clustering, off-chain signals, and anything you can
invent. Do not wait to be asked to look somewhere new. If a vector might reveal
another wallet, pursue it, measure whether it works, and either wire it in or
write down why it does not.

**Two independent vectors agreeing is the strongest evidence available**, because
the ways they can be fooled do not overlap: an amount coincidence does not also
fake a shared deposit address, and a shared deposit address does not also fake a
trading style. Never promote a wallet on one vector alone.

---

## Vectors that work today

| Vector | Where | Notes |
|---|---|---|
| Observed transfer | `transfer_graph.py` | On-chain and HL-native movement |
| Linkage | `linkage.py` | Shared funder, shared CEX deposit address, first-gas funding. **Address reuse is the strongest single signal** — a CEX deposit address belongs to one account |
| Amount correlation | `correlator.py` | Exit re-appears as a same-size deposit across a CEX gap |
| Behavioural | `scanner.py`, `fingerprint.py` | Trading style. **Currently unvalidated — see below** |
| HL-native | `ledger_analyzer.py` | Two-way flow entirely inside Hyperliquid, invisible to L1 |
| Shared agent | `agent_links.py` | An agent is authorised BY the account — two accounts sharing one are the same operator. Strong enough to CONFIRM alone |
| Dormancy handoff | `dormancy.py` | One wallet goes quiet, another is born. The only vector needing NO connection between them |
| HyperEVM watch | `scripts/check_hyperevm.py` | Nonce tripwire; history there cannot be reconstructed after the fact |

Unified in `roster.py` (tiers on how many vectors agree) and `accounting.py`
(what fraction of outflow is actually explained).

## Vectors collected but NOT wired into detection — pursue these

- **`data/agents/`** — HL API agent wallets. An agent is *explicitly authorised*
  by an account. An agent shared between two accounts is close to proof of common
  control. Collected by `collector.py`, used by nothing. **Highest-value unused
  signal.**
- ~~`data/orders/`~~ — **wired 2026-09-10** as the `order_profile` dimension.
  54,866 records were being collected and never read. It discriminates hard:
  the target is 94.6% `Ioc` limit slices with **0% cancels and 0% client order
  ids** (manual TWAP), while all three correlation leads are ~100% cancels and
  **100% client order ids** (bots). Now IN the backtest too, split by the same
  calendar days as the fills (verified disjoint: 0 shared oids, 0 shared days),
  with strangers given the same dimension so the target cannot score on one
  nobody else could earn.
- ~~`data/twitter/`~~ — **removed 2026-09-10**. It was three empty directories
  with no collector and no reference anywhere: scaffolding for an intention
  never built, not data going unused. X's API is paid and scraping is fragile
  and ToS-questionable, so it is not being built. The free equivalent of the
  same idea — a human-chosen label that travels between accounts — is agent
  NAMING SCHEMES, now in `agent_links.naming_families`.
- `data/vaults/`, `data/referral/`, `data/subaccounts/` reach `scanner.py` but
  only for leaderboard candidates, not for graph-discovered wallets.

## Vectors worth inventing

Think about these; none is implemented:

- **Naming schemes beyond agents** — vault names and referral codes are also
  human-chosen. `naming_families` handles agents; the same idea applies there.
- ~~Dormancy handoff~~ — **built 2026-09-10** (`src/dormancy.py`,
  `scripts/check_dormancy.py`). Calibrated on HIS rhythm: median gap 2d, p90 5d,
  longest ever 21d, so a fixed "dormant after a week" rule would cry wolf. Two
  alerts: an unusual/unprecedented silence, and a wallet whose FIRST activity
  lands inside one. Live: silent 4d (normal), 7 anomalous gaps in his history,
  no handoffs among 13 candidates.
- **Portfolio correlation** — returns or position-basket correlation between the
  target and a candidate over the same window. Two wallets holding the same
  unusual basket at the same time is hard to fake.
- **Amount signatures** — he moves amounts like `8999999.00021` and
  `5005314.50`. Exact repeated fractional amounts are a habit, and habits travel.
- **Approval fingerprints** — which routers/contracts a wallet approves, in what
  order, is a durable behavioural trace on L1.
- **Gas and fee habits** — priority-fee setting is a per-human default.
- **Counterparty-set overlap** — Jaccard over the full counterparty set, not just
  deposit addresses.
- **Liquidation and leverage habits**, cross vs isolated margin preference.
- **Rare-market co-presence** — already partly used via `xyz:` markets; extend to
  any market with few participants.

---

## Hard-won rules — violating these has already cost real findings

1. **Empty is not blind.** A wallet showing "no outbound" usually just has not
   been swept. `records_for` on an unswept wallet returns only rows where it
   touched something already swept. Check `swept_wallets()` before concluding a
   trail ended. This produced a false $509M narrative.
2. **A token is its contract, not its ticker.** Anyone can deploy "USDC".
   Pricing by symbol booked **$3.07B of counterfeit value as real** — more than
   half of every dollar figure in the system. Registry:
   `data/labels/token_contracts.json`. Only add contracts verified on-chain or
   against an explorer; a wrong entry quarantines real money.
3. **Verify "well-known" addresses.** Twice the commonly cited address was a
   different token — Polygon's cited USDT is `USDT0`; Optimism has **two**
   legitimate contracts both reporting `USDC`.
4. **Never tune the thing that validates you.** The self-match backtest still
   FAILS, but on one condition rather than two: he is now **rank 1** (his own
   best match) with a margin of **+0.0361** against a required **+0.05**. It got
   there by adding independent signal — order-submission habits, windowed to
   avoid leakage — not by moving weights or lowering the bar. Closing the last
   0.0139 by reweighting, or by relaxing the 0.05, would fit the one measurement
   that proves the scorer works. Treat any behavioural score as unvalidated until
   `profile/backtest.json` has `passed: true`.
5. **A failed read must never serialise as a clean result.** Distinguish
   "we could not tell" from "there is nothing there", everywhere.
6. **Never price a missing value as `0.0`** — zero is invisible to every
   threshold. Use `None`.
7. **Reach beats tidiness.** Do not classify a wallet as infrastructure to clean
   up a report if it costs graph depth. Conduit detection is deliberately a
   single pass for this reason.
8. **A transfer is not ownership.** Surface leads; never assert identity.

---

## Operating facts

- **Alerts arrive as GitHub Issues, not email.** Brevo SMTP has never once
  delivered (account unactivated). `_github_issue_fallback` in `alerts.py`.
- Free tiers only. Etherscan free does not serve account endpoints for
  **base, bsc, optimism** — those chains are unreadable, not empty.
- HyperEVM public RPC caps `eth_getLogs` at 1000 blocks against ~1s blocks:
  history there is unreconstructable, so it must be *watched*, not swept.
- Tests must stay network-free and must never write to real `data/`.
- Verify before claiming: run it, read the output, report what it actually says.
