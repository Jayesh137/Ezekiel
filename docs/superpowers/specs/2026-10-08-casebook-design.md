# Casebook — keep every suspect forever, and rank them by how likely each is his

**Date:** 2026-10-08 · **Status:** written as the record of an autonomous build. The operator asked
for the work to be finished to a high bar without check-ins (`operator_working_style`); every
judgement call is listed in §16 with what it costs if it is wrong. Push, PR and merge wait for the
operator. · **Request (verbatim):** "save all possible (likely) matches found in a database to keep
forever/to review in the future (could be in a year+ time), also do heavy analysis on them and rank
by likelyhood its him, improve in any and all ways you can think of, make everything perfect, highly
effective and useful - think from first principles"

---

## 1. Why — measured 2026-10-08, read-only

| Measurement | Result |
|---|---|
| The four strongest leads in project history (`0xdd53c529…`, `0x5b5d5120…`, `0xb83de012…`, `0x12e16e3d…`) | All **WATCH, 0 vectors, 0 reasons** in today's roster, at positions 601–902 of 2,735. `peak_tier` (PROBABLE/POSSIBLE) is the only trace that anything was ever found |
| `0x4aecac3b…`, the HyperCore deposit address the trace engine found on 2026-10-04 | WATCH #1,458, 0 vectors |
| `0xdd53c529…`'s dormancy handoff (score 0.4286, born two days into a six-day silence) | **Gone.** His p90 gap moved from 5 to 6 days as his history grew, so the six-day silence is no longer "anomalous" and the dormancy detector, which checks the wallet every run, stopped reporting it. The fact is permanent; only the yardstick moved |
| Roster history | **593 versions** of `data/roster/latest.json` since 2026-09-10; 118–300 lead rows per version early on. Nothing reads them |
| The per-wallet detector budget (`detector_candidates`, 40 slots, used by dormancy, identity, agents, HL surface) | Two slots go to **token contracts**: USDC on Base (`0x833589fc…`, rank 12) and USDC.e on Polygon (`0x2791bca1…`, rank 17). Their `funding_route` registry observations (65 for USDC) rank them above every vector-less lead. `data/candidates/` also holds the zero address (154 KB of observations) and Polygon's `0x…1010` precompile |
| What `portfolio` returns for an address Hyperliquid does not know | `ok`, eight windows of 11 synthetic `0.0` points, `vlm` 0.0, no birth. For a real account, `allTime` carries its whole value history from birth (42 points for `0xdd53c529…`) plus day/week/month/all-time volume — a life story in one weight-20 call |

**Conclusion.** The project finds suspects and then forgets them. Every detector writes a
`latest.json` that is rewritten whole; the roster is rebuilt from scratch every run by design; and
Hyperliquid itself forgets (≈ two weeks of fills, 300 explorer actions). A wallet whose evidence
lapses — a correlation window rolls forward, a cap cuts it, a yardstick moves — becomes
indistinguishable from 2,000 strangers, and nothing re-reads it to notice when it wakes up. The
first wallet he migrates to may already have been suspected once, and nobody would know.

## 2. First principles

1. **A reading is not a memory.** A detector's file says what is true *now*. Whatever must survive a
   lapse — that something was once seen, when, how strong, and what it rested on — needs its own
   permanent, append-only home. Rule 5 over time: "we no longer see it" is not "it never happened",
   and the record has to say which of the two it is.
2. **Likelihood is a ratio, and most of ours are assumptions.** The honest form is Bayesian:
   posterior odds = prior odds × the likelihood ratio of each independent piece of evidence. A few
   LRs are measured (the candidate study's calibrated tooling LRs); the rest are declared, with a
   band, a basis and a reason, in one versioned table a reader can audit a year from now. Nothing is
   fitted to the story (rule 4): the table is fixed before any score is read, and the recall check
   (§7.6) is reported, never tuned to.
3. **Dependence is the main way a score lies.** Two items sharing a mechanism are one piece of
   evidence (the study's rule: never multiply tests inside a family). Two families in the same
   category (two kinds of money link) are partly dependent and are discounted (§7.3). This mirrors
   the roster's "two financial vectors are not two votes".
4. **Evidence against must be able to move the score down.** Calibrated tooling AGAINST, a style
   veto, copier-shaped timing and an operator's "not him" all subtract. A ranking that can only rise
   confirms itself (the GCR-hypothesis rule).
5. **A lapse is not a refutation, and the score must know which it is.** A detector that re-checked
   the wallet and no longer finds the evidence has refuted it; a detector that simply stopped
   looking has not. Only the first removes the evidence from the best estimate.
6. **The archive must stay useful, not become a museum.** A suspect that wakes up — opens a
   Hyperliquid account, starts trading after a month of silence, grows to millions — while he goes
   quiet is the migration this project exists to catch. Re-reading every suspect on a slow rotation
   costs one weight-20 call each.
7. **Self-describing and durable.** Plain JSON in git (every version kept by git itself), a README in
   the directory, every number stamped with the model version that produced it, every item carrying
   a one-line human summary. A person or a fresh session must be able to read it in a year without
   this conversation.
8. **Doctrine unchanged.** Ground truth is config only; a transfer is not ownership (rule 8); a
   failed read is never empty (rule 5); a missing value is never 0 (rule 6); the target is never his
   own suspect; one writer per file; the casebook casts no roster vote and changes no tier.

## 3. Scope

**In:** a permanent case file per suspect wallet; an append-only event log; a likelihood ranking
with bands; operator clusters; a sleeper watch (periodic Hyperliquid re-reads, wake alerts); a
one-off backfill from git history; a not-a-wallet filter applied to the roster, the route index and
the casebook; pinning the best suspects into the per-wallet detectors; a dashboard page, a phone tab
and a CLI with a SQLite export.

**Out:** new detectors or vectors; any change to roster tiers or votes; the candidate study's
selection or tests (its Phase 2 is being built by a separate routine from its own spec); real-world
identity of any kind (the deliverable is a Hyperliquid address — `execution_fingerprint` scope line).

## 4. Architecture

```
 trace.yml (after "Build wallet roster")
 ───────────────────────────────────────
 scripts/update_casebook.py                         the ONLY writer of data/casebook/**
   1. read roster/latest.json (+ config, dormancy, study for refutation coverage)
   2. admit + extract evidence per roster row            src/casebook/extract.py
   3. merge into case files: first/last seen, lapse, refute   src/casebook/cases.py
   4. probe suspects on Hyperliquid (portfolio, budgeted)    src/casebook/vitals.py
   5. clusters + likelihood bands                            src/casebook/score.py
   6. write changed case files, append events, write index   src/casebook/store.py, report.py
   7. alert on a suspect waking                              src/alerts.py
            │
            ├──► data/casebook/latest.json ──► dashboard /casebook, phone "Ranked" tab
            └──► roster.detector_candidates pins the top suspects (next run)

 scripts/backfill_casebook.py  — once, locally: every historical roster version through steps 2-3
 scripts/casebook.py           — read-only CLI: top, show, events, check, sqlite export
 src/not_wallets.py            — "this address cannot be anyone's trading account"; used by roster,
                                 route index and casebook
```

| Module | Responsibility |
|---|---|
| `src/casebook/model.py` | evidence kinds, families, categories, LR table with bands and bases, prior, model version |
| `src/casebook/extract.py` | admission rule; a roster row → evidence items (one code path for live and backfill) |
| `src/casebook/cases.py` | case-file merge: open, observe, lapse (24 h debounce), refute, return, roster status, events |
| `src/casebook/score.py` | item statuses → family values → category discount → now / central / ceiling; clusters; calibration checks |
| `src/casebook/vitals.py` | `portfolio` → vitals and life series; wake events (opened, woke, grew, emptied) |
| `src/casebook/store.py` | paths, strict reads, write-if-changed, monthly event files, state |
| `src/casebook/report.py` | index rows, headline, next useful checks, bounded sizes |
| `src/not_wallets.py` | system addresses, precompiles, canonical token contracts, the pricing registry, config exclusions |

**Single-writer map.** `update_casebook.py` (trace.yml) writes `data/casebook/**` and nothing else;
`backfill_casebook.py` writes the same tree but refuses a non-empty casebook without `--rebuild`
and is run by hand only. The roster reads `data/casebook/latest.json` (detector pins) and writes only
the roster.

## 5. The case file — `data/casebook/cases/<address>.json`

```json
{
  "schema": "casebook-case/1",
  "address": "0xdd53c5297309130ab5fe5623dc905752e3342b13",
  "opened_at": "2026-09-11T07:40:12Z",
  "opened_by": ["tier:POSSIBLE", "dormancy_handoff"],
  "known": null,
  "ruling": null,
  "roster": {
    "tier": "WATCH", "peak_tier": "PROBABLE", "peak_at": "2026-09-12",
    "tiers": [["2026-09-11", "POSSIBLE"], ["2026-09-12", "PROBABLE"], ["2026-09-16", "WATCH"]],
    "last_in_roster": "2026-10-08",
    "reasons": ["…the latest non-empty reasons the roster gave…"],
    "reasons_at": "2026-09-15"
  },
  "evidence": {
    "dormancy_handoff": {
      "kind": "dormancy_handoff", "family": "lifecycle", "direction": "for",
      "status": "historical", "origin": "backfill",
      "first_seen": "2026-09-11T07:40:12Z", "last_seen": "2026-09-27", "seen_days": 17,
      "absent_since": "2026-09-27T09:12:00Z", "refuted_at": null,
      "strength": 0.4286,
      "facts": {"score": 0.4286, "gap_length": 6, "delay_days": 2, "candidate_first_day": 20682},
      "summary": "First active 2 days into a 6-day silence of his (handoff score 0.43)"
    }
  },
  "hl": {
    "probed_at": "2026-10-08T01:10:00Z", "probe_ok": true, "on_hl": true,
    "birth_ms": 1787174400062, "total_value": 52061448.83,
    "day_volume": 3880812.83, "week_volume": 16855822.76, "month_volume": 121473569.34,
    "all_time_volume": 249586537.38,
    "life": [[1787174400062, 0.0], [1791419654475, 52061448.83]],
    "probes": [["2026-10-08", 52061448.83, 121473569.34, 3880812.83]]
  },
  "links": {"operator_group": null, "referral_pairs": [], "frontend_agent": "0x…"},
  "score": {"model": "casebook-model/2026-10-08.1", "now": -3.0, "central": -2.6, "ceiling": -0.9,
            "solo_central": -2.6, "cluster": null, "families": {"lifecycle": {"now": 0, "central": 0, "ceiling": 0.79}}},
  "score_days": [["2026-10-08", -3.0, -2.6, -0.9]]
}
```

Rules:
- **Day granularity for "still true".** `last_seen` and the tier list are dates, so a case whose
  evidence merely persists is not rewritten every run (git churn). A file is written only when its
  canonical serialisation changes.
- **Facts are the strongest observation**, bounded (strings ≤ 200 chars, lists ≤ 5, ≤ 1.5 KB per
  item). `strength` is the detector's own number where it has one (score, confidence, USD).
- **`hl.life`** is the `allTime` value history as Hyperliquid returns it (≤ 60 points, refreshed on
  every probe). **`hl.probes`** keeps one row per probe day: the last 120, then one per month.
- **`score_days`** keeps one row per day the score changed: the last 400, then one per week.
- A case file that is present but unreadable is **never overwritten** (rule 5): the run reports it
  and leaves it for a human.

## 6. Evidence

### 6.1 Admission

A roster row opens a case when it is not the target, not INFRASTRUCTURE or a service, not a
non-wallet (§9), and any of:
- tier or `peak_tier` is CONFIRMED, PROBABLE or POSSIBLE, or it carries any vector;
- it carries an evidence kind of §6.2 that is not context-only;
- it is in `config.known_self_wallets`, `config.watch_wallets` or `config.study_wallets`.

Not admitted alone: graph reach, a behavioural score that casts no vote, portfolio overlap, a
HyperEVM nonce, `funding_route` registry observations, the successor-investigations queue
(`investigation_utility_not_probability`, mostly `insufficient_data`), newborns without another
signal. A row that fails the non-wallet test goes to the index's `rejected` list with its reason,
so the record shows it was seen and why it was set aside.

Once open, a case is **never closed**. A case whose wallet leaves the roster keeps everything, and
its items lapse by the rules below.

### 6.2 Evidence kinds (from a roster row's `vectors`, `evidence` and `reasons`)

Cluster = the target plus `known_self_wallets`. Items with a value in **Context** carry no LR.

| Kind | Family | Read from | Notes |
|---|---|---|---|
| `subaccount_of_cluster` | control | `evidence.subaccount_of` ∈ cluster; `explicit_links` kind subaccount with a cluster wallet | protocol fact |
| `agent_of_cluster` | control | `explicit_links` kind agent with a cluster wallet | protocol fact |
| `staking_link` | control | `explicit_links` kind staking_link with a cluster wallet | protocol fact |
| `shared_agent` | control | `evidence.shared_agents` | protocol fact |
| `direct_transfer` | money | `transfer` vector (own, not via group); facts: `totals`, `depth`, `chains`, reasons | |
| `two_way_flow` | money | `hl_native` vector, or `totals` with ≥ $1K each way | |
| `funded_by_target` | money | `shared_first_funder` == target, or the gas-funding reason | |
| `circle_flow` | money | `evidence.circle_flows` | protocol-recorded |
| `boundary_transfer` | money | `evidence.boundary` rows voting `transfer` | protocol-recorded |
| `boundary_member` | money | `evidence.boundary` rows without a transfer vote | weak |
| `trace_reach` | money | `evidence.trace_reach` | weak |
| `private_deposit_address` | infrastructure | `evidence.shared_private_deposit_address` | |
| `hl_deposit_address` | infrastructure | `evidence.shared_hl_deposit_address` | |
| `quiet_payee` | infrastructure | `evidence.shared_quiet_payee` | |
| `quiet_first_funder` | infrastructure | `evidence.shared_first_funder` ≠ target | |
| `linkage_graph` | infrastructure | `linkage` vector not explained by the four above | |
| `amount_correlation` | gap | `correlation` vector + `correlation_confidence` | strength = confidence |
| `dormancy_handoff` | lifecycle | `evidence.dormancy_handoff` | strength = score |
| `study_tooling` | tooling | `evidence.study.families.tooling` with a calibrated verdict | **measured LR** |
| `execution_program` | tooling | `evidence.execution_program` | context when a study tooling item exists |
| `behavioural_vote` | behaviour | `behavioural` vector | |
| `style_veto` | behaviour | `evidence.style_vetoes` non-empty | **against** |
| `referral_with_cluster` | association | `evidence.referral_links` | declared fact |
| `comovement` | coactivity | `evidence.comovement.verdict` | `same_hand` for, `copier` **against** |
| `operator_not_him` | ruling | `config.casebook_rulings[address].verdict == "not_him"` | **against** |
| `portfolio_overlap`, `graph_reach`, `operator_group`, `referral_pair`, `behavioural_score`, `hyperevm_nonce` | — | the matching evidence keys | **Context** |

A vector the row holds only through its operator group (`vectors_via_group`) is not re-extracted
from that row: the cluster union (§7.5) carries it once.

### 6.3 Item status

| Status | Meaning | Counts in now / central / ceiling |
|---|---|---|
| `current` | in the latest roster read | low / mid / high |
| `standing` | no longer reported, but a **protocol fact** (control family, `referral_with_cluster`): an approval, sub-account or declared code happened and cannot un-happen | low / mid / high |
| `lapsed` | observed live by the casebook, absent ≥ 24 h, cause unknown (window, cap, rewrite) | 0 / **½ mid** / high |
| `refuted` | absent after the detector **re-checked the wallet** (dormancy: in `collection.checked` of a run after the item went missing; study tooling: studied with a different verdict) | 0 / 0 / high |
| `historical` | from the backfill, not current when the casebook went live | 0 / 0 / high |
| `invalidated` | the current not-a-wallet or service filter now rejects what the item rests on | 0 / 0 / 0 |

**Debounce.** An item absent from one roster read sets `absent_since`; it becomes `lapsed`
(or `historical` in the backfill) only after 24 h absent, so a flapping item is not a stream of
events. Seen again after lapsing → `current`, event `evidence_returned`.

**A stale or unreadable roster changes nothing.** The roster's `computed_at` must be newer than the
last one consumed (`state.json`), or no item lapses that run (rule 5: a failed read must never
serialise as "the evidence is gone").

## 7. Likelihood

### 7.1 The model

```
log10 posterior odds = log10 prior odds + Σ_category [ strongest family + ½ × Σ other families ]
family value         = combine(item values)   — max of supports, min of againsts,
                                                and the larger in magnitude when both exist
probability          = 1 / (1 + 10^-odds)
```

**Prior: 1 : 1,000** (log10 −3) for a wallet in the casebook before its own evidence is read: the
casebook holds O(10³) suspects and he plausibly has O(1) unknown wallets among them. The rank does
not depend on the prior; the probability column does, and says so.

Three bands per case: **now** (current and standing items at their low LR — the defensible floor),
**central** (the best estimate: current/standing at mid, lapsed at half mid, rank key), **ceiling**
(every item not invalidated, at its high LR — "the most it could be").

### 7.2 The LR table (log10; low / mid / high) — `casebook-model/2026-10-08.1`

| Kind | low | mid | high | Basis |
|---|---|---|---|---|
| `subaccount_of_cluster`, `agent_of_cluster` | 3.0 | 4.0 | 5.0 | assumed — protocol-declared control (CLAUDE.md: confirms alone) |
| `staking_link`, `shared_agent` | 2.0 | 3.0 | 4.0 | assumed — one key or one declaration across accounts |
| `direct_transfer`, `circle_flow`, `boundary_transfer` | 0.3 | 0.7 | 1.3 | estimated — 2 of the 412 L1 counterparties (≥ $50K) of his three config wallets are his (2026-10-06); OTC desks, market makers and exchanges dominate (rule 8) |
| `two_way_flow` | 0.7 | 1.0 | 1.7 | assumed — a relationship, which his own wallets have and OTC desks also have |
| `funded_by_target` | 0.7 | 1.3 | 2.0 | assumed — gas or first funding of a fresh wallet is a self-funding shape |
| `boundary_member`, `trace_reach` | 0.0 | 0.3 | 0.7 | assumed — holds money that passed through his world |
| `private_deposit_address` | 0.7 | 1.5 | 2.0 | assumed — a CEX deposit address belongs to one exchange account (address reuse is the strongest single linkage signal); an OTC payer is the alternative |
| `hl_deposit_address` | 0.7 | 1.3 | 2.0 | assumed — same argument inside Hyperliquid |
| `quiet_payee`, `quiet_first_funder`, `linkage_graph` | 0.3 | 0.7 | 1.3 | assumed — shared quiet infrastructure, rule 9 already applied upstream |
| `amount_correlation` (confidence c) | log(1+2c) | log(1+9c) | log(1+30c) | assumed — every correlation lead so far was a bot or a coincidence |
| `dormancy_handoff` (score s) | log(1+2s) | log(1+9s) | log(1+20s) | assumed — ~9% of accounts born during his history land in one of his anomalous windows by chance (7 gaps × 3 days of ~240) |
| `study_tooling` (study LR λ) | log λ | log λ | log λ + 0.3 (for) / ½ log λ (against) | **measured** — the study's calibrated LR (Clopper–Pearson bound, spec 2026-10-06 §8.3) |
| `execution_program` | 0.7 | 1.3 | 2.0 | assumed — census-gated match of his clip table |
| `behavioural_vote` | 0.0 | 0.3 | 0.7 | assumed — votes only when the scorer is validated |
| `style_veto` | −1.0 | −0.5 | −0.2 | assumed — a positive finding of a different human |
| `referral_with_cluster` | 0.3 | 0.7 | 1.3 | assumed — chosen by the user of the code, quiet codes only |
| `comovement` same_hand / copier | 0.0 / −0.7 | 0.2 / −0.4 | 0.5 / 0.0 | assumed — the one timing reading a copier cannot fake |
| `operator_not_him` | −3.0 | −3.0 | −2.0 | the operator's ruling |

Family clips: control [−3, +5], ruling [−3, 0], every other family [−2, +2].

### 7.3 Categories (the dependence discount)

financial = money, infrastructure, gap · protocol = control, association · behaviour = tooling,
behaviour, coactivity · successor = lifecycle · ruling = ruling. Inside a category, families are
sorted by magnitude; the first counts in full, every other at one half. Two kinds of money link are
not two independent votes — the roster's own rule, expressed as a weight.

### 7.4 Known wallets

`known_self_wallets` are cases too (their story matters, and they are the recall check), marked
`known: "config:known_self"`, shown pinned above the ranking with "known" in place of a
probability. Their ranking position is computed from evidence alone, ignoring the config fact.

### 7.5 Clusters

Union-find over suspect–suspect control relations: operator groups (`evidence.operator_group`,
`subaccount_of` between suspects) and explicit links between two non-cluster suspects. Known wallets
are never merged (their links are evidence items). A cluster is one operator: its score is computed
over the union of its members' items (per family, the strongest member's value — never a sum), and
every member shows the cluster's score with its own `solo_central` beside it. Referral pairs and
shared funders are listed as **related**, not merged: an association is not control.

### 7.6 Calibration checks (reported in the index, never tuned to)

- **Recall:** each known wallet's evidence-only rank among all cases. If the model cannot put his
  own wallets near the top from their evidence, its LRs are wrong — and the remedy is new evidence
  or a recorded model revision, never a quiet reweighting.
- **Coherence:** Σ central probability over unknown cases. If the model is calibrated it is the
  expected number of his wallets in the casebook (≈ 0.5–3); far above that means inflated LRs.

## 8. Sleeper watch

Each run probes cases with `portfolio` (weight 20) through a strict reader (`utils.hl_read`'s `ok`
flag; a failed read is recorded as `probe_ok: false` and changes nothing else), inside
`ReadBudget(seconds=150, weight_per_minute=600)` — about 70 probes. Order:

1. never probed;
2. top 25 unknown cases by central score, probed > 12 h ago;
3. on Hyperliquid, probed > 3 days ago;
4. not on Hyperliquid, probed > 7 days ago;
5. everything else, oldest first.

*As implemented, the due top 25 come first and a failed read is retried after an hour (§18).*

**Events** (each compared with the case's previous probe; on a first probe only the recent past
counts, so the first sweep is a baseline, not an alert storm):

| Event | Condition |
|---|---|
| `hl_opened` | previously not on Hyperliquid (or never probed and born < 7 days ago) → now has value or volume |
| `hl_woke` | previous `month_volume` was a real 0.0 → now `week_volume` > 0 |
| `hl_grew` | value crossed $1M from below $250K since the previous probe (first probe: within 7 days, from `life`) |
| `hl_emptied` | value fell from ≥ $100K to < $1K (event only, no alert) |

**Alert** `alert_casebook_wake` for `hl_opened`, `hl_woke` and `hl_grew` on an unknown case without
a `not_him` ruling that is in the top 25 or has central odds ≥ −2 (≈ 1%): **CRITICAL** when the
dormancy report says he is in an unusual silence (the dormancy-handoff signal, the same severity
`alert_dormancy_handoff` uses), **HIGH** otherwise. Cooldown 168 h per wallet and event kind.

## 9. Not a wallet — `src/not_wallets.py`

`classify(address, *, config, token_contracts, activity=None) → reason | None`:
- the zero address, `0xff…ff`, Hyperliquid system addresses (`0x2000…0000`, `0x2222…2222`, the
  `0x20…` token range), precompiles (`0x00…0001`–`0x00…ffff`, which includes Polygon's `0x…1010`);
- canonical token contracts: USDC, USDC.e, USDT and WETH on Ethereum, Arbitrum, Base, Optimism and
  Polygon, WHYPE on HyperEVM — each confirmed against Blockscout before it is listed (rule 3);
- every contract in `data/labels/token_contracts.json`;
- `config.excluded_addresses` and `config.known_service_addresses`.

Used by **the roster** (such a row becomes INFRASTRUCTURE with `service_reason` "not a wallet: …",
which frees detector slots), **the route indexer** (no `funding_route` observation for a non-wallet
recipient), and **the casebook** (admission). Ground truth is immune: the target and known wallets
are never classified.

## 10. Backfill — `scripts/backfill_casebook.py`

Walks `git log --reverse -- data/roster/latest.json` through one `git cat-file --batch` process, one
version in memory at a time, and feeds each version through the same `extract` → `merge` path as the
live update with `now` = the commit time and `origin: "backfill"`. Lapses use the same 24 h debounce
and become `historical`. Events are written into the months they happened. It refuses to run on a
non-empty casebook without `--rebuild`. `--limit N` and `--since` support a quick look; the full run
is ~593 versions. The first live run then confirms which items are current.

## 11. Outputs

- **`data/casebook/cases/<address>.json`** — §5.
- **`data/casebook/events/<YYYY-MM>.jsonl`** — one line per change: `case_opened`, `evidence_new`,
  `evidence_lapsed`, `evidence_refuted`, `evidence_returned`, `tier_changed`, `score_moved` (central
  moved ≥ 0.5), `hl_opened`, `hl_woke`, `hl_grew`, `hl_emptied`. Each line: `at`, `address`, `kind`,
  `detail`, `origin`. Append-only.
- **`data/casebook/latest.json`** — `schema`, `computed_at`, `model` (version, prior, the whole LR
  table with bases, categories, clips), `counts`, `calibration` (§7.6), `target` (his dormancy
  state), `cases` (one compact row per case, ranked), `rejected` (≤ 500), `run` (roster consumed,
  items, probes, failures, budget).
- **`data/casebook/state.json`** — the roster `computed_at` last consumed, run stats.
- **`data/casebook/README.md`** — the format, statuses, scoring and how to query, for a reader in a
  year. Static; not written by the job.

## 12. Alerts, feed health, integration

- `alert_casebook_wake` (§8), routed by its severity token like every other alert.
- **Feed health:** `OTHER_FEEDS["casebook"] = ("casebook/latest.json", "computed_at", 720)`; blind
  when it holds 0 cases while the roster holds leads, or when ≥ half a run's probes failed.
- **Detector pins:** `roster.detector_candidates` pins, after the operator's own pins, up to **8**
  unknown, Hyperliquid-present cases from the casebook ranking whose central odds beat the prior —
  never trimmed. The best suspects stay under dormancy, identity, agents and HL-surface checks even
  after the roster has forgotten them. An unreadable index pins nothing.
- **trace.yml:** step "Update the casebook" after "Build wallet roster", `timeout-minutes: 6`
  (internal budget 150 s; lookups 10/30 s < budget < step < job).
- `scripts/compact_data.py` lists `casebook` as irreplaceable; `tests/conftest.py` probes
  `data/casebook` so no test writes to it.

## 13. Dashboard, phone, CLI

**Dashboard `/casebook`** (Hunt group, after Roster). Header: counts, model version, prior, the two
calibration checks, freshness. Filters: Hyperliquid accounts only (default on), include known
wallets, search by address. Ranked table: rank, wallet (Hypurrscan), likelihood (central % with the
now–ceiling band), family chips (↑/↓, faded when lapsed), HL value and last activity, peak tier,
first seen. Case panel: score waterfall (prior → each family → posterior), evidence timeline
(first → last seen, status), the life series with his anomalous silences shaded, tier history,
cluster and related cases, next useful checks.

**Phone `/review`:** a third tab, **Ranked** — the top 25 unknown Hyperliquid suspects by central
likelihood, each with its probability band, a one-line why, HL value and last activity, one tap to
Hypurrscan. A case whose `last_change` is newer than the last time the tab was opened shows a dot
(`localStorage`, wrapped in try/catch). Missing index → "casebook unavailable", never "no suspects".
`dashboard/ARCHITECTURE.md` §8 gains the fields it reads.

**CLI `scripts/casebook.py`:** `top [-n N] [--all]`, `show <address>`, `events [--since D]
[--address A]`, `check` (every case parses, index consistent), `sqlite [PATH]` (tables `cases`,
`evidence`, `events`, `probes`; default `data/.local/casebook.sqlite3`, gitignored) — the
"database" view for ad-hoc SQL, rebuilt from the JSON in seconds.

`VITE_RAW_BASE` (optional) overrides the raw-data base URL so the dashboard can be verified against
a local tree before the data reaches `main`.

## 14. Testing and verification

Network-free tests; nothing writes to the real `data/`.

- `test_not_wallets.py`: each class; ground truth immune; roster marks a token contract
  INFRASTRUCTURE; the route indexer skips it.
- `test_casebook_extract.py`: admission (and what is not admitted); every kind from a roster row,
  including old roster shapes; group vectors not re-extracted; cluster checks on explicit links.
- `test_casebook_cases.py`: open; first/last seen; strongest facts kept; 24 h debounce; lapse vs
  historical; refutation from dormancy coverage; return; stale roster changes nothing; tier days;
  bounded lists; events.
- `test_casebook_score.py`: family combine (for, against, both); clips; category discount; bands
  by status; prior; clusters (union, no known merge, strongest-member families); recall and
  coherence checks; model table complete for every kind.
- `test_casebook_vitals.py`: unknown-address portfolio → not on HL; birth; volumes; life bounds;
  each wake event; first-probe baseline (no storm); a failed probe changes nothing.
- `test_casebook_store.py`: write-if-changed; unreadable case never overwritten; monthly events
  append; index bounded.
- `test_update_casebook.py`: an end-to-end run on a fixture tree; probe ordering and budget; alert
  routing (CRITICAL in a silence, HIGH otherwise, cooldown key).
- `test_backfill_casebook.py`: a fake history of three roster versions → statuses, events in their
  months, refusal without `--rebuild`.
- `test_casebook_cli.py`: top, show, events, check, sqlite on a tmp casebook.
- `test_roster_casebook_pins.py`: pins after the operator's, bounded, deduplicated, unreadable → none.
- Feed health, conftest probe, `casebook.test.js`, `review.test.js` (Ranked tab), `api.test.js`.

**Before merge:** the backfill run on the real history; a live dry run of the update with a small
probe budget into a scratch directory; `python -m ruff check src/ tests/ scripts/`; the full suite;
`npm test`, `npm run build`, `npm run check:pwa`; the page and the phone tab rendered in headless
Chrome against the local tree.

## 15. Delivery

One PR: not-wallet filter → casebook core → update script + workflow + alerts + feed health →
detector pins → backfill run and data → CLI → dashboard page → phone tab → docs (README, CLAUDE.md
row and rules, incident-log entry, `ARCHITECTURE.md` §8).

## 16. Rulings (decided without a check-in; what each costs if wrong)

1. **JSON in git, not a committed SQLite file.** Text diffs, one writer, every version kept by git,
   readable in a year; SQLite is an on-demand export. *If wrong:* the operator wanted a database file
   to open directly — `scripts/casebook.py sqlite` produces one in seconds.
2. **The roster is the casebook's only evidence source.** It already applies every vote rule and
   carries per-mechanism evidence keys, and its 593 versions make the backfill the same code path.
   *If wrong:* a finding the roster drops before writing (none known today) never reaches the
   casebook; adding a direct reader later is one extractor.
3. **Rank by central, show now and ceiling.** *If wrong:* assumed mids order the list; the now band
   is beside every row and the model table says which LRs are assumed.
4. **Historical (backfilled) evidence counts only in the ceiling.** Many early roster votes were
   retracted (seven false PROBABLEs on 2026-09-16; counterfeit-priced totals before 09-17), and old
   rows lack the facts to re-judge them. *If wrong:* a true old lead ranks low — it is still a case,
   still probed, and its ceiling and history are one tap away.
5. **Lapsed live evidence counts at half its mid in the central estimate.** *If wrong:* a lapse
   caused only by coverage is under-weighted by half; a lapse caused by a silent retraction is
   over-weighted by half. Refutation (§6.3) removes the cases we can detect.
6. **Prior 1:1,000.** *If wrong:* probabilities shift uniformly; the order does not change.
7. **Wake alerts limited to the top 25 or ≥ 1% central.** *If wrong:* a low-ranked suspect waking is
   recorded as an event and shown, but does not page.
8. **Detector pins: 8.** *If wrong:* 8 of 40 detector slots go to old suspects instead of the
   roster's newest rows; the roster still fills the other 32.
9. **Behavioural-only and route-registry wallets are not cases.** *If wrong:* the casebook misses a
   wallet that only the unvalidated scorer liked; the scan history keeps those.

## 17. Out of scope, and measured-no

- **A measured dormancy base rate.** `data/newborn` stores only an age bucket ("week", "month"), not
  a birth date, so the rate of births inside his windows cannot be measured from it today.
- **Successor investigations as a source** — research-only, `insufficient_data` for 100 of 100.
- **Changing tiers or votes from the casebook** — evidence memory is not evidence; the roster stays
  the only voter.

## 18. As implemented (2026-10-08)

- **The index is `data/casebook/latest.json`,** not `index.json`: every watched feed is a
  `latest.json` (feed health and the dashboard's feed list both require it).
- **A transfer vote with no valued flow with the target** (cast through his config wallets,
  or on an unpriced movement) says "Observed transfer with the target / one of his wallets
  (N movements, M unpriced)" and carries no strength: the real backfill showed "received $0,
  sent $0" for them, and $0 is not the amount of an unpriced movement (rule 6).
- **Index rows carry `why`** (the reason alone) beside `headline` (reason plus the
  Hyperliquid line, for the CLI and alerts), because the phone card and the dashboard show
  the Hyperliquid line in their own place. The reason is the case's own current evidence,
  else its operator cluster's current evidence (naming the member), else its own history,
  else the roster's words.
- **The roster grades only well-formed addresses as keyless;** malformed test identifiers
  were never its question. Canonical tokens were confirmed on Blockscout (Ethereum,
  Optimism) and by reading `symbol()`/`name()` on-chain (Arbitrum, Base, Polygon, WHYPE on
  HyperEVM); three test fixtures had used HYPE's system address or WHYPE as wallets.
- **Measured:** the backfill read 592 roster versions (1 unreadable) in 7 minutes at ~63 MB
  into 831 cases, 3,264 events, 5.4 MB. Recall: his known wallets rank 3rd among 697 unknown
  cases on evidence alone; coherence: the unknown cases' probabilities sum to 0.78. A live
  dry run on the newest roster read 30 suspects on Hyperliquid at ~2 s each (0 failures, no
  events: a first read is a baseline) and wrote 228 case files in ~110 s including the
  60 s probe budget.
- **The committed casebook** is that backfill plus live merges of the 2026-10-08 02:31
  roster (with those 30 reads) and the 04:27 roster (no reads), so the first run on `main`
  consumes the next roster.
- **`invalidated` is re-judged every run, roster or not, and is not a stored status.** An
  item resting on another address (a first funder, a quiet payee, a deposit address)
  keeps its `status` and gains `invalid_reason` when today's whole-chain readings
  (`labels/address_activity.json`, by the roster's own `services_from_activity`) call
  that address a service, or `not_wallets` calls it keyless; it then counts as
  `invalidated` (0 / 0 / 0) everywhere it is shown, and its reason line says what stopped
  counting and why rather than repeating the roster's words. A new measurement that
  passes the address clears the reason; an address the table does not hold, or a table
  that cannot be read, keeps the stored verdict (rule 5). The first live replay (roster
  2026-10-08 04:27) invalidated **4** items, all "shares his first funder" on the
  2.28M-transaction exchange hot wallet `0xf92402bb...` that the referral twins share
  (`0x5b5d5120...`, `0x12e16e3d...` and two others): items from roster versions before
  2026-09-16, when the roster still reported a funder nobody had measured. So the funder
  item's summary no longer claims "measured quiet".
- **One percentage format everywhere** (`report.pct`: >99%, 34%, 3.4%, 0.34%, <0.1%);
  the wake alert had printed `%.2g`, which reads "1e+02%" above 99.5%.
- **The due top 25 are probed first,** in rank order (never read, a failed read an hour
  old, or a read 12 h old), then never-read cases, failed reads, accounts after 3 days and
  the rest after 7. Never-read first held the top 25 behind a backlog of ~670 at go-live:
  replayed on the 2026-10-08 15:58 roster, the old order's first 70 reads held no top-25
  suspect; the new order reads all 25 first.
- **An unreadable `state.json` is set aside, never read as a first run:** moved whole to
  `state.unreadable-<UTC time>.json` (its sticky rejections exist nowhere else); the run
  exits 1 and writes a fresh state (re-reading the roster is harmless), and
  `casebook.py check` names the kept file until a human deletes it.
- **A family's state is the best status among the case's own items that carry a value
  in it,** else `cluster`: an item that no longer counts carries nothing, so it cannot
  label "invalidated" a chip whose value a linked account supplies.
- **The next checks say how the evidence ended** (has lapsed, was refuted, is history, no
  longer counts), naming only what applies.
- **A counterpart Hyperliquid knows is exempt from the contract verdict, never the busy
  one,** as in the roster's `services_from_activity`: the casebook's own `portfolio` read
  or the roster's `userRole` (anything but missing). Code on another chain does not unmake
  an account. No such counterpart existed when this landed.
- **What only the 2026-09-10 rosters reported no longer counts** (`cases.void_pre_fix`, every
  run and at the end of the backfill). Every roster that day (the first in git, 06:27-15:33
  UTC) predates its fixes: counterfeit tokens priced as real (07:37), and token quantities
  booked as dollars (1,030,689,918 MAX as $1.03B), records counted three times and contracts
  read as deposit addresses (17:54). The next roster ran the fixed code (2026-09-11 15:59),
  so `last_seen == "2026-09-10"` marks evidence only the faulty rosters reported. Money,
  `linkage_graph` and `amount_correlation` items like that gain `invalid_reason`; a peak
  taken that day is dropped, and the `evidence_peak_voided` event keeps the figure; the
  reason line names the kind instead of repeating a void amount; a later report lifts it.
  Replayed on `main`'s casebook (2026-10-08 20:53): 36 items invalidated (32 direct
  transfers, 4 linkage), 23 peaks voided, and no case's central or now band moved. Only
  ceilings fell (23 cases), so ties at the base odds reorder: `0x207700bd...` (the $1.03B)
  and three `0x160...` look-alikes credited with counterfeit USDC went from ranks 84-109 to
  about 605.
- **A forgery of his declared wallets is excluded on every run** (`cases.exclude_forgery`,
  the live update and the backfill): an address with the same first and last four hex
  characters as the target or a `known_self_wallets` entry (`spam.lookalike_of`) and no
  money item of $1,000 or more with him is address poisoning. The roster now grades such
  rows INFRASTRUCTURE too, but five of the eight on `main` (2026-10-08) came from roster
  history and are listed by no roster today, so the casebook checks every case itself.
  `0xf078170f...f19e` had ranked #3 on spoofed fake-USDC transfers "from" the target.
