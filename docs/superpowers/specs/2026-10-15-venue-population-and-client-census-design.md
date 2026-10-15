# Venue population, client-fingerprint census and outcome markets — design

Date: 2026-10-15. Status: implementing on `feat/venue-client-census`.

## Why

The behavioural stack cannot find him as it stands, and the reason is reach,
not resolution. The sharpest test the project owns — his *tooling* (SDK
`market_open`: taker IOC priced 5% through the book, ~1.7 s cadence, no client
id, one round clip per coin) — is applied to 25 candidates a run drawn from the
leaderboard and registries, which see only large or already-suspected accounts.
The tape screens his 43 markets only; a second account trading other coins is
invisible by construction. And the 12-dimension style scorer has been voteless
since 2026-09-26 (`passed: null`, 0 of 20 controls accumulated) with features
thousands share.

Measured today: a venue-wide fills sample (every coin, `fills: [{}]`) costs
7.7 MB for five minutes in ONE request — 31,482 fills, 1,261 takers, 383 coins
(79 `xyz:`, 8 `para:`, 7 `io:`, 1 `mkts:`, 27 `@` spot pairs and dozens of
`#<n>` HIP-4 outcome contracts). Zero of those 1,261 takers ran a program-shaped
burst in the window — the shape is rare venue-wide. `historicalOrders` answers
2,000 orders a wallet (weight ~120 with rows) and names the client class
directly: the target is 1,806 `Limit/Ioc` without client id + 194
`Market/FrontendMarket` over 21 days; the treasury is 74 `FrontendMarket` + 2
`Gtc` (web UI, by hand); `0xf078969e…` is 2 `FrontendMarket` with a `0xba5ed1…`
client id (the BasedApp frontend). **So his known wallets do not run his script:
they are operated by hand on the web UI.** That bounds what any tooling census
can find, and it is recorded as such.

## What is built

### 1. Venue population — `src/venue_sample.py`, `scripts/sample_venue.py` (tape.yml)

A bounded venue-wide fills sample each run (3 minutes, ~5 MB) folded into
`data/tape/venue_population.json`: per taker, first/last seen, fills, distinct
coins, HIP-3 dexes used, outcome-market fills (`#` coins), spot fills, and
program-shaped runs on ANY coin at ANY clip (`execution_program.program_runs`
over orders reconstructed by hash). Bounded at 20,000 wallets, least recently
seen evicted. Three populations fall out of it and are reported: outcome-market
traders (the instrument his history says he favours and his main account has
never touched), program-shaped takers venue-wide, and the active taker set that
feeds the census below. Outcome traders also present in two or more of his
treasury sessions are registered as candidates (`outcome_treasury`).

### 2. Client-fingerprint census — `src/client_census.py`, `scripts/census_client_fingerprint.py` (tape.yml)

For wallets from the venue population, the tape sketches and the registry
(priority: program-shaped, then rare-market takers, then the rest; never read
twice within 14 days), one `historicalOrders` read each under `hl_budget`, a
compact client profile per wallet in `data/client_census/profiles.json`:
orders, span, shares of `Ioc`-no-cloid, `FrontendMarket`, cloid, `Gtc`/`Alo`,
trigger/TPSL, reduce-only; cloid prefix family; round-size share; IOC cadence
runs (1.2–3 s, ≥15 orders). `his_class` = Ioc-no-cloid share ≥ 0.8 with at least
one cadence run — the shape of his script. The report measures the class's base
rate over everything read (the number the casebook has lacked), lists the
wallets in his class, registers them (`client_census`) so the execution-program
detector reads their fills and measures the 5% offset and clip table, and
records the known wallets' classes (web UI) and a split-half reading of the
target (older vs newer 1,000 orders) as the controls. No vote.

### 3. Wiring

tape.yml gains both steps (job timeout raised accordingly); the
execution-program detector's registry sources accept `client_census`,
`venue_program` and `outcome_treasury`; digest gets one line; Tripwires one card.

## Rulings (each with its cost if wrong)

- 3-minute venue sample per run: enough for population, too short for clip
  tables on quiet coins (the per-market tape keeps that job for his markets).
- `his_class` at 0.8 Ioc-no-cloid with a cadence run: looser admits other SDK
  users (the detector then measures the exact offset and clips), tighter would
  miss a rescaled or partly manual run.
- 14-day re-read interval: a client switch within two weeks is seen late.

## Not built

- Reading all venue fills continuously (60 MB per 20 minutes): the per-market
  tape already covers his markets; the population only needs a sample.
- A 5%-offset measurement inside the census: it needs fill prices per order
  (`userFills`, another ~100 weight), which the detector already does for the
  wallets the census hands it.
