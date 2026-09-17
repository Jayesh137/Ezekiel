# Ezekiel relay — setup (about five minutes, once)

The relay runs free on Google's servers, so Ezekiel needs no always-on machine.
Every 5 minutes it:

1. **keeps the GitHub workflows on schedule** — GitHub's own timers arrive a
   median of 198 minutes apart on this repo; the relay dispatches watch every
   10 min, collect 15, trace 30, scan 60, analyze daily, never into a busy queue
   (queueing into a busy queue is what gets runs cancelled);
2. **watches his wallets** — the target, the treasury and `0xf078969e…` — and
   pushes an **urgent** ntfy within ~5 minutes of a withdrawal or send to an
   address that is not his, a new agent, a sub-account or a vault. His routine
   moves between his own wallets are not alerted. Measured over his history:
   about one alert a month.

If it ever loses access to GitHub or Hyperliquid it tells you on ntfy (once a
day), so it cannot die silently.

## 1. A GitHub token (only needed for part 1)

1. Open <https://github.com/settings/personal-access-tokens/new> (fine-grained token).
2. **Token name:** `ezekiel-relay`. **Expiration:** 1 year.
3. **Repository access:** *Only select repositories* → `Jayesh137/Ezekiel`.
4. **Permissions → Repository permissions → Actions:** *Read and write*.
   Nothing else.
5. **Generate token** and copy it.

This token can start and read workflow runs on this one repository. It cannot
read or change code, secrets or settings.

## 2. The script

1. Open <https://script.google.com> → **New project**. Name it `Ezekiel relay`.
2. Delete the placeholder code, then paste the whole of
   `scripts/apps_script/ezekiel_relay.gs` from this repository. Save (Ctrl+S).
3. **Project Settings** (gear icon) → **Script Properties** → add:
   - `NTFY_TOPIC` — your ntfy topic (the one your phone subscribes to;
     the same value as the repo's `NTFY_TOPIC` secret).
   - `GITHUB_TOKEN` — the token from step 1. (Leave it out to run the wallet
     tripwire only.)
4. Back in the editor, choose the function **`setup`** in the toolbar and click
   **Run**. Google asks for permission ("connect to an external service",
   "run when you are not present") — allow it. You get an ntfy message:
   **Ezekiel relay installed**.

That is all. **Executions** (left sidebar) shows each 5-minute run and its log.

## 3. Afterwards

- The PC dispatcher is no longer needed. Remove it any time with
  `schtasks /Delete /F /TN "Ezekiel workflow dispatcher"` — the two never run
  a workflow twice, because each only dispatches when the newest run is finished
  and older than its interval.
- When the token expires, the relay sends **Ezekiel relay cannot read GitHub**;
  make a new token and replace the `GITHUB_TOKEN` property.
- To stop the relay: **Triggers** (clock icon) → delete the `tick` trigger.

## Limits it stays inside

Google's free quotas: 20,000 URL fetches and 90 minutes of trigger runtime a
day. The relay uses about 4,000 fetches and 25–45 minutes.
