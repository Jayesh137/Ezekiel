# The casebook

Every wallet this project has ever suspected of being one of the target's, kept for
good, re-read on Hyperliquid on a rotation, and ranked by how likely each is his.

- Target: `0x45d26f28196d226497130c4bac709d808fed4029` (Hyperliquid).
- Design: `docs/superpowers/specs/2026-10-08-casebook-design.md`.
- Written by one program only: `scripts/update_casebook.py`, run by
  `.github/workflows/trace.yml` after the roster. The history it started from was
  recovered once from git by `scripts/backfill_casebook.py`.

Nothing here is proof of ownership. A case is a question the project asked and the
evidence it found; the likelihood orders the questions.

## Reading it

```
python scripts/casebook.py top            # the ranked suspects on Hyperliquid
python scripts/casebook.py top --all      # every case, his known wallets and excluded ones included
python scripts/casebook.py show 0x...     # one case in full: evidence, dates, score, Hyperliquid life
python scripts/casebook.py events --since 2026-10-01
python scripts/casebook.py check          # every file reads; the index matches the cases
python scripts/casebook.py sqlite         # a SQLite database at data/.local/casebook.sqlite3
```

The dashboard's **Casebook** page and the phone app's **Ranked** tab read the same files.

## Files

| Path | What it is |
|---|---|
| `cases/<address>.json` | One case per suspect (`schema: casebook-case/1`). Never deleted. |
| `events/<YYYY-MM-DD>.jsonl` | Every change, one JSON object per line: a case opened, evidence new / lapsed / refuted / returned, a tier change, the score moving, a suspect waking on Hyperliquid. Append-only. |
| `latest.json` | The ranked index (`schema: casebook-index/1`), rebuilt every run. It carries the whole likelihood model (`model`), the calibration checks (`calibration`), counts, the target's dormancy state and what the run read. |
| `state.json` | The last roster reading consumed, the sticky list of rejected addresses, the last run. |
| `state.unreadable-<time>.json` | Only after a fault: a `state.json` that could not be read, set aside whole by the run that found it (which exits 1 and writes a fresh one). Its rejected list exists nowhere else, so recover what you need by hand, then delete the file; `python scripts/casebook.py check` names it until then. |

## A case

- `opened_at`, `opened_by`, `origin` (`backfill` or `live`): when and why it was opened.
- `known`: `config:known_self` for his configured wallets (ground truth). They are cases
  too, and serve as the recall check, but are never ranked among the suspects.
- `ruling`: the operator's ruling from `config.casebook_rulings`, if any.
- `excluded`: set when today's filters call the address a service or not a wallet. The
  case is kept and leaves the ranking.
- `roster`: the roster's tier by day (`tiers`, changes only), the peak tier, and the
  last non-empty reasons it gave, kept even after the roster stopped giving any.
- `evidence`: one item per kind, each with `status`, `first_seen`, `last_seen`,
  `seen_days`, the latest `facts` and `summary`, and the strongest observation beside
  them (`peak_*`). An item that rests on another address (a first funder, a quiet payee,
  a deposit address) gains `invalid_reason` when the whole chain has since measured that
  address a service, or it has no key: the item keeps its `status` for the record and
  counts as `invalidated`.
- `hl`: the last Hyperliquid read (`portfolio`): whether it is an account, birth, value,
  volumes, the value history from birth (`life`) and one row per probe day (`probes`).
- `score`: the current bands and the family values that made them; `score_days` keeps
  one row per day the score changed.

## Evidence status

| Status | Meaning | Counts in now / central / ceiling |
|---|---|---|
| `current` | in the latest roster reading | yes / yes / yes |
| `standing` | a protocol fact no longer reported (an approval, a sub-account, a declared code cannot un-happen) | yes / yes / yes |
| `lapsed` | seen live, then absent 24 hours or more, cause unknown | no / half / yes |
| `refuted` | absent after a detector re-checked the wallet and found nothing | no / no / yes |
| `historical` | from the git-history backfill and not current when the casebook went live | no / no / yes |
| `invalidated` | not stored as a status: an item with `invalid_reason`, whose address was since measured a service (busy, or a contract Hyperliquid does not know as an account) or has no key. Re-judged every run against `data/labels/address_activity.json`; a new measurement that passes the address clears it, and an address with no new measurement keeps its verdict | no / no / no |

## How the likelihood is computed

```
log10 posterior odds = prior + for each category: strongest family + half of every other family
family value         = the max of its supports, the min of its againsts,
                       the larger in magnitude when it has both
probability          = 1 / (1 + 10^-odds)
```

- **Prior:** 1 in 1,000 for a wallet in the casebook before its own evidence is read.
  The order does not depend on it; the percentages do.
- **Bands:** `now` counts current and standing items at the low end of their ratio (the
  defensible floor); `central` adds lapsed items at half and is the rank key; `ceiling`
  counts everything not invalidated at the high end ("the most it could be").
- **Families and categories:** each evidence kind belongs to a family (control, money,
  infrastructure, gap, lifecycle, tooling, behaviour, association, coactivity, ruling).
  Items in one family share a mechanism, so a family takes its strongest item and never
  a sum. Families in one category (for example, two kinds of money link) are partly
  dependent, so every family after the strongest counts half.
- **Ratios:** `latest.json` → `model.kinds` lists every kind's band, its basis and the
  reason. Only the candidate study's tooling ratio is **measured**; the direct-transfer
  ratio is **estimated** from this project's own counts; the rest are **assumed** and
  say why. Evidence against (a calibrated study AGAINST, a style veto, copier-shaped
  timing, an operator ruling) moves the score down.
- **Calibration checks** (`latest.json` → `calibration`), reported and never tuned to:
  where his known wallets rank among the unknown cases on their evidence alone, and the
  sum of the unknown cases' probabilities (about 0.5 to 3 if the ratios are honest).

Change the model only by bumping `MODEL_VERSION` in `src/casebook/model.py` and
recording why in `docs/incident-log.md`, never to move one wallet.

## The sleeper watch

Each run re-reads the cases that are due on Hyperliquid (`portfolio`, about 70 per run):
the top 25 first, in rank order, every 12 hours (sooner if never read or a read failed),
then never-read cases, failed reads after an hour, accounts every 3 days, addresses with
no account every 7 days. A suspect that opens an account, starts trading after 30
quiet days, or grows past $1M raises an event; for the top 25, or anything at about 1%
and up, it also pages: CRITICAL while he is in an unusual silence, HIGH otherwise.

## Ruling on a case

Add to `config.json`:

```json
"casebook_rulings": {
  "0x...": {"verdict": "not_him", "note": "a market maker: client ids on every order", "date": "2026-10-08"}
}
```

`not_him` subtracts three orders of magnitude and keeps the case. To declare a wallet
his, add it to `known_self_wallets`: ground truth lives in config only.
