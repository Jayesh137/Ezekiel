# Wallet Continuity Tracker

Extend the existing transfer graph into a multi-hop continuity tracker that keeps
following the target when funds are routed through intermediary wallets to obscure
the next active trading wallet.

Goal: surface **leads**, never assert ownership.

---

## 1. Limitations proven in the current code

Each measured against `origin/main`, not asserted.

| # | Limitation | Evidence |
|---|---|---|
| L1 | Depth capped at 3 | 4-hop chain A→B→C→D: node D absent, `max_depth` default 3 |
| L2 | Expansion never passes hop 1 | frontier seeded only from `e["src"] == target`; hop-2 wallets never looked up |
| L3 | No resume | `health.expansion` persists `frontier_remaining` but no queue; each run restarts traversal |
| L4 | **Value flow ignored** | flat 0.25/hop penalty. A 4-hop chain retaining 97% scores **0.0 at depth 2 and 3** |
| L5 | No relay detection | no pass-through helper exists in the module |
| L6 | No first-class paths | only `path: [addr, addr]`; no per-hop evidence, no `chains` key |
| L7 | No lifecycle | five classifications, no state machine, no trading timestamps |
| L8 | Split/merge unused in graph | `find_split_correlation` exists but `build_graph` never calls it |
| L9 | Bridge breaks the chain | bridge edges are `bridge_event`, excluded from adjacency; nothing links pre- to post-bridge |

L4 is the decisive one: today the exact evasion pattern produces zero confidence.

---

## 2. Architecture

New pure module `src/continuity.py` holds the path model, scoring and lifecycle.
`src/transfer_graph.py` keeps orchestration, expansion and persistence. This
follows the existing pure-core / IO-wrapper split and stops `transfer_graph.py`
(already 1059 lines) growing unbounded.

```
tracer / ledger / correlator / linkage
        │  normalised edges
        ▼
transfer_graph.py     BFS, iterative expansion, budget, persistence
        │  edges + node evidence
        ▼
continuity.py         paths → signals → score → lifecycle      (pure, testable)
        │
        ▼
data/transfer_graph/latest.json   nodes[] + chains[] + health{}  (schema v2)
        ▼
/transfers dashboard
```

### Persisted schema (v2)

`schema_version: 2`. Readers tolerate v1 (missing `chains`/`lifecycle`) so the
current dashboard keeps working; writers always emit v2.

```jsonc
"chains": [{
  "id": "sha1 of ordered hop refs",
  "endpoint": "0x…",
  "hops": [{ "src","dst","chain","asset","amount_usd","ts","ref",
             "discovery_source","confidence_contribution" }],
  "hop_count": 4,
  "elapsed_hours": 31.5,
  "value_start_usd": 1000000, "value_end_usd": 940000, "value_retained": 0.94,
  "relay_hops": ["0x…"], "breaks": [{ "at":"0x…","reason":"bridge crossing" }],
  "confidence": 0.71, "reasons": [...], "blockers": [...]
}]
```

---

## 3. Continuity scoring model

Signals are grouped into **families**. Promotion requires corroboration from
**two or more distinct families** — this is the central false-positive guard.

| Family | Signals |
|---|---|
| `FLOW` | amount similarity after fees, temporal proximity, split/merge reconciliation, value retained |
| `FUNDING` | first-gas relationship, funded-before-trading, direct funding by target |
| `BEHAVIOUR` | fingerprint similarity, wallet rotation (old quiet → new active) |
| `STRUCTURE` | repeated transfers, two-way flow, shared uncommon counterparty or route |
| `PLATFORM` | HL-native transfer, independent L1 evidence, bridge correlation |

Score = Σ(signal weights, capped per family) × `hop_decay` × `age_decay`.

- `hop_decay` is **value-aware**, replacing the flat penalty:
  `hop_decay = max(0.25, value_retained ** 0.5 × 0.9 ** (hops − 1))`.
  A 4-hop chain retaining 94% decays to ~0.71, not 0.0. A 4-hop chain retaining
  3% decays to ~0.25.
- `age_decay` reuses the existing `recency_factor` — flat 90 days, linear to a
  0.4 floor. Applied to FLOW/STRUCTURE only; BEHAVIOUR and FUNDING do not age.

### Hard rules (each test-covered)

1. A single transfer never links ownership, at any amount.
2. Volume alone never promotes — magnitude is not a signal.
3. One behavioural similarity alone never promotes.
4. A service node scores 0.0 and is never traversed for identity.
5. Promotion to a successor state requires ≥2 signal families.
6. A path break (service, mixer, unresolved bridge, missing data) caps the path
   below `HIGH_CONFIDENCE_SUCCESSOR` and is recorded in `breaks[]`.
7. Contradictory behavioural evidence (style veto) blocks promotion outright.

---

## 4. Lifecycle rules

Monotonic except `DORMANT` and `REJECTED_SERVICE`, which are terminal-ish and
re-evaluated each run.

| State | Entry condition |
|---|---|
| `REJECTED_SERVICE` | classified SERVICE (config or fan-degree). Terminal. |
| `LEAD` | appears on any path from the target |
| `OBSERVED` | ≥2 transfers, or seen across ≥2 runs |
| `FUNDED_BY_TARGET` | received value from the target on a path with no break |
| `TRADING_STARTED` | funded, and has Hyperliquid fills after first funding |
| `POSSIBLE_SUCCESSOR` | `TRADING_STARTED` + continuity ≥0.40 + ≥2 signal families |
| `HIGH_CONFIDENCE_SUCCESSOR` | `POSSIBLE_SUCCESSOR` + continuity ≥0.65 + no break + no veto + central disposition returns ALERT |
| `DORMANT` | previously ≥`FUNDED_BY_TARGET`, no activity for 60 days |

Tracked per wallet: first/last transfer, first/last trading, funding source,
behavioural score, inbound/outbound value, known paths, strongest evidence,
blockers, contradictions, `state_changed_at`.

---

## 5. Multi-hop, split/merge, bridge

**Multi-hop.** Iterative level-by-level BFS to `max_depth` (default 4). Frontier
priority = `traced_value × relay_likelihood × recency`, so budget goes where a
chain is most likely. Every frontier decision records
`expanded | deferred | suppressed | stopped` with a reason.

**Relay detection.** A wallet is a relay when it forwarded ≥70% of received value
within 72h to ≤3 destinations. Relays are chased hard and their hops are treated
as *evidence of deliberate routing*, not noise.

**Split/merge.** `find_split_correlation` is lifted into `build_graph`: an outflow
split across N wallets that reconverges within tolerance and window is
reconciled into one path with `split_merge` recorded in FLOW.

**Bridge.** A bridge crossing becomes a *typed break*, not a dead end. It may
carry the lead forward only when withdrawal and deposit correlate on amount
(after fees) and timing; confidence is multiplied by the correlation confidence
and the break is always listed in `breaks[]`, capping the path below
`HIGH_CONFIDENCE_SUCCESSOR`.

**Resume.** `frontier_queue` persists unexplored wallets with their priority and
depth. Next run resumes rather than restarting. A partial traversal is always
reported `frontier_incomplete: true`.

---

## 6. Alerts

Routed through the existing `thresholds.disposition` — no new bypass. Fires only
on material change: new high-confidence successor, a lead starts trading,
confidence crosses a band, or funds advance a hop. Dedup key is the **path
signature** (ordered endpoint set), so equivalent paths across wallets and runs
alert once. Failed delivery keeps the path in `undelivered_alerts` and does not
advance path-alert state, reusing the retry mechanism already added.

Wording is fixed to "fund-flow-linked", "possible successor", "high-confidence
continuity lead". Never "owns" or "same person".

---

## 7. Testing

Deterministic, offline, fixture-driven. Covers all fifteen required scenarios
including 4-hop decay, split-then-reconverge, bridge with fees and delay,
service non-strengthening, large one-way transfer, gas-funded-then-trading,
rotation, stale decay, incomplete frontier honesty, API failure preserving
partial graph and resume cursor, alert retry, path dedup, contradictory
behaviour, and no regression on the known linked wallet.

---

## 8. Safety and scope

Public data only — Hyperliquid's public API and Etherscan with the configured
key. No scraping, no credentials beyond configured secrets, no identity
attribution. Historical evidence is never mutated or rewritten; schema v2 is
additive. Production cooldown and calibration state are untouched.
