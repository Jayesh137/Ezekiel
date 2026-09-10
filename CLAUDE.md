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
  **100% client order ids** (bots). **Not in the self-match backtest**: both
  windows would draw from the same order pool, so that dimension would score
  ~1.0 by leakage and falsely inflate the validation. Wiring it there needs
  orders split by the same time windows — worth doing, carefully.
- **`data/twitter/`** — not referenced anywhere in `src/`. Off-chain signal,
  entirely unexploited.
- `data/vaults/`, `data/referral/`, `data/subaccounts/` reach `scanner.py` but
  only for leaderboard candidates, not for graph-discovered wallets.

## Vectors worth inventing

Think about these; none is implemented:

- **Dormancy handoff** — wallet A goes quiet, wallet B starts within hours. The
  clearest migration signature there is, and cheap to compute from fills.
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
4. **Never tune the thing that validates you.** The behavioural self-match
   backtest **currently FAILS** (self 0.5522 vs a stranger's 0.5571). Fixing it
   by reweighting dimensions would fit the one measurement that proves the scorer
   works. Treat any behavioural score as unvalidated until
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
