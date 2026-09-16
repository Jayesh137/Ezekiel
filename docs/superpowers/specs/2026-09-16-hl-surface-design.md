# HL account surface for every candidate — design

2026-09-16. Approved direction: a standalone detector (approach 1), then the
contract-grading bug, then two cleanups.

## Why

Three Hyperliquid endpoints — `subAccounts`, `referral`, `userVaultEquities` —
were asked only about the TARGET (collector) and the ≤6 watched wallets. For
the target they are empty (`null`, no code, no referrer, `[]`), so the
scanner's vault/referral overlap checks return early for every wallet, every
run. Measured live over the 25 strongest roster wallets:

- **31 sub-account addresses under 5 roster wallets, none of them in the
  roster, the graph or the identity cache.** `0x7fdafde5…`'s sub-account
  "funding-test" holds $1.26M. A sub-account is an address the owner can copy
  on Hyperliquid directly — the deliverable's exact shape — and nothing read it.
- **4 wallets name a referrer, and one referrer is itself on the roster:**
  `0xfe7ce058…` (dropped from PROBABLE) was referred by `0xa312114b…` (WATCH).
- Vault deposits: only HLP, which is shared by everyone (rule 9).
- The scanner's `_check_referral_link` reads `referrerAddress`/`referredUsers`,
  keys the API does not return, and swallows every failure as "no link".

## Units

**`src/hl_surface.py` — pure.** No network, no file writes except `save`.

- `parse_subaccounts(payload, master) -> list[dict] | None` — `None` payload
  (the API's real "none") → `[]`; a list → rows
  `{address, master, name, account_value}`; anything else → `None` (unreadable).
- `parse_vaults(payload) -> list[str] | None` — list → vault addresses; else `None`.
- `referral_reading(payload) -> dict | None` — a populated dict → `{referred_by,
  code, referred: [addresses]}` via `src/referral.py`'s parsers; `{}`/non-dict
  → `None`. A successful read is always a populated document (measured), so
  `{}` is only ever a failure — the `webData2` rule.
- `build_report(readings, referrer_sizes, cluster, shared_vaults) -> dict` —
  derives `subaccounts`, `referrals`, `vault_deposits` and `links`.
- `new_links(previous, current) -> list[dict]` — links absent from the previous
  report. An absent previous report is an empty one (see Alerts).

**`scripts/check_hl_surface.py` — I/O.** Shaped like `check_agents.py`.

## Who is asked

`[target] + known_self_wallets + detector_candidates(config, roster, 120)`,
deduplicated. Pinned wallets survive the cap (that function's contract).

Three calls per wallet through `cctp_feed.strict_post` (timeout 30s, 2
retries), paced 0.15s. **Not `utils.hl_post`**: its failure sentinel for
`userVaultEquities` is `[]`, byte-identical to "deposits into no vault", so a
timeout would serialise as a measured absence — rule 5.

Then one `referral` call per distinct referrer found, to learn how many
accounts used that code (`len(referralStates)`), capped at 30 per run and
cached in the report for 7 days. The count is what separates a friend's code
from an influencer's — rule 9 for referrals. **Unmeasured means excluded**:
an unmeasured referrer never makes a vector.

Internal time budget 240s, checked between wallets; wallets not reached are
listed in `not_reached`, never recorded as empty. Step `timeout-minutes: 6`
(nesting: per-call 30s < budget 240s < step 360s).

## Output — `data/hl_surface/latest.json`

```
computed_at, cluster[], wallets_checked, unreadable, not_reached[],
readings:      {wallet: {subaccounts: [..]|null, vaults: [..]|null,
                         referral: {referred_by, code, referred[]}|null}}
subaccounts:   {sub: {master, name, account_value}}
referrals:     {wallet: {referrer, referrer_accounts|null}}
referrer_sizes:{referrer: {accounts, measured_at}}
vault_deposits:{wallet: [vault, ...]}   # shared vaults removed
links:         [{kind, address, linked_to, why, ...}]
```

`null` in `readings` = could not tell. `[]` = measured none.

## How it counts in the roster

**Cluster = config only** (target + `known_self_wallets`), as
`check_identity.cluster()`. A roster tier is an inference; letting one
detector's inference promote through another is the coupling the 2026-09-16
linkage fix removed.

1. **Sub-account of a cluster wallet → `explicit_link` (CONFIRMs alone).**
   Hyperliquid only lets a master create a sub-account; this is the same fact
   `userRole` reports, read from the other side, and emitted in identity's
   link shape (`kind: "subaccount"`). Reverse too: a cluster wallet being a
   candidate's sub-account names that candidate.
2. **Operator groups.** A master and its sub-accounts are one operator by
   construction. The roster unions the vectors of every member of a group
   and tiers each member on the union, recording `operator_group` and
   `vectors_via_group`. This is not double counting: the vectors still come
   from distinct detectors, attributed to accounts one person provably runs.
   Services never join or lend vectors to a group.
3. **Sub-account rows enter the roster** (so every per-wallet detector reads
   them next run) when they hold ≥ $1,000 or belong to a cluster wallet or to
   a CONFIRMED/PROBABLE master. An empty sub-account has nothing to copy; it
   is still in the report and joins the moment it is funded.
4. **Referral with the cluster → new vector `referral`, ONE vote.** Either the
   wallet was referred by a cluster wallet, or a cluster wallet was referred by
   it — and the code has ≤ 10 accounts on it (measured, not assumed). A code is
   chosen by the person using it, so it is association, not control: never
   CONFIRM-alone. A larger or unmeasured code is evidence only.
5. **Referral between two non-cluster wallets** (`0xfe7ce058 ← 0xa312114b`):
   evidence only, `referral_pair` on both rows.
6. **Vault deposits**: evidence only, shared vaults (HLP,
   `hl_shared_destinations`) removed first.

## Alerts

Routable severities only (`CRITICAL`/`HIGH`).

- **CRITICAL** — a sub-account link to the cluster not in the previous report:
  a new Hyperliquid address of his. Cooldown 168h, keyed on the sub-account.
- **HIGH** — a quiet referral link to the cluster not in the previous report.
- Nothing for candidate-only findings; they move tiers, and a tier reaching
  PROBABLE enters the close watch, which alerts on its own terms.

An absent previous report counts as EMPTY, not as a first reading to seed.
Measured today the cluster has no sub-accounts and no referral links, so
seeding would buy nothing and an arriving link must never be swallowed — the
size-ratio decision, not the `extraAgents` one.

## Scanner

`_load_target_referral_addresses` / `_check_referral_link` rewritten over
`src/referral.py`'s parsers (`referred_by`, `referred`). `_check_vault_overlap`
drops shared vaults. Failures print rather than vanish.

## Wiring

`trace.yml`: "Check HL account surface" after "Check agents", before the
roster, with `!cancelled() && steps.deps.conclusion == 'success'`.

## Testing (network-free, never real `data/`)

Parsers across null/list/failure; build_report for each link kind including
the reverse sub-account link and the quiet/public/unmeasured referral split;
new_links with absent previous; the roster group union, service exclusion and
the $1,000 row rule; the scanner parsers against the real payload shape;
check_hl_surface end-to-end with a fake post (a raised read is `null` and
counted, budget exhaustion lists `not_reached`); the workflow step.
