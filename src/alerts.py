# src/alerts.py
"""Email alert system via Brevo SMTP.

Credentials come from three environment variables, all supplied as GitHub secrets:

    BREVO_SMTP_LOGIN   SMTP username — the login shown in Brevo under
                       Settings -> SMTP & API -> SMTP, of the form
                       <id>@smtp-brevo.com. NOT the Brevo account email.
    BREVO_SMTP_KEY     SMTP password — an SMTP key from the same page.
                       NOT the account password and NOT a v3 REST API key.
    ALERT_EMAIL        Recipient, and the From address. Brevo will reject a
                       From address that is not a verified sender on the account.

This previously authenticated as the literal username "apikey", which is
SendGrid's convention — Brevo does not accept it and answered every send with
535 5.7.8 Authentication failed, no matter how valid the key was.
"""

import json
import os
import smtplib
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.links import address_line, address_path
from src.utils import (
    DATA_DIR,
    atomic_write_json,
    now_ms,
    read_cursor,
    save_latest,
    write_cursor,
)

SMTP_HOST = "smtp-relay.brevo.com"
SMTP_PORT = 587  # STARTTLS

# Once a send fails (e.g. bad SMTP credentials), it will keep failing for the
# rest of this run. Short-circuit so a batch of alerts doesn't attempt hundreds
# of dead SMTP connections and blow the job timeout / spam the log.
_smtp_disabled_this_run = False


def _record_delivery(subject: str, delivered: bool, reason: str | None = None) -> None:
    """Persist alert delivery health where the operator can actually see it.

    Email cannot report its own failure, and a failed send returns False without
    failing the job — so a dead output channel looks exactly like a quiet week
    from the Actions tab, the data and the dashboard alike.

    That is not hypothetical. On 2026-08-12 an audit found 25 candidates promoted
    to ALERT across the scan history, not one alert cursor ever committed (they
    are written only after a successful send), and the SMTP Delivery Check run
    once — on 2026-07-27 — and failed. Six months of collection and three
    detection vectors feeding an output that went nowhere, with every scan run
    reporting success.

    EACH RUN WRITES ITS OWN SHARD, since 2026-09-12. This used to be a
    read-modify-write on one shared file, which was safe only because every
    committing workflow shared the `data-commit` concurrency group and so never
    overlapped. Once watch.yml got its own group that stopped being true, and
    the push step settles a conflict with `git rebase -X theirs` — which takes
    the pushing run's file WHOLE. Two runs computing counters from the same
    base, one of them winning, erases the other's record:

        run A  base healthy=true -> send FAILS -> writes healthy=false
        run B  base healthy=true -> suppressed -> writes healthy=true
        B pushes second, B wins, A's failure is gone

    which is precisely the outage this file exists to make visible. So the
    durable record is now data/alerts/runs/<run id>.json, which two runs can
    never collide on, and latest.json is a DERIVED cache recomputed from every
    shard on each write. The dashboard and its tests see an unchanged shape; a
    rollup discarded by a rebase now costs nothing, because the shards it was
    built from all survive and the next write rebuilds it.

    Never raises: a diagnostic that can take down the thing it diagnoses is
    worse than no diagnostic.
    """
    try:
        now = datetime.now(UTC).isoformat()
        # An alert no channel was ever going to carry reports nothing about the
        # channels — see _health_bearing. It is still counted and still kept in
        # `recent`, so suppression stays visible; it just leaves every health
        # field describing the last alert that actually had somewhere to go.
        suppressed = not delivered and not _health_bearing(subject)
        status = "delivered" if delivered else ("suppressed" if suppressed else "failed")
        event = {"at": now, "subject": subject, "delivered": delivered,
                 "status": status, "reason": reason}

        _migrate_legacy_record()
        _append_to_shard(event)
        _prune_shards()
        save_latest(str(DATA_DIR / "alerts"), derive_health(load_shard_events()))
    except Exception as e:  # noqa: BLE001 - must never break alerting
        print(f"[alerts] could not record delivery health: {type(e).__name__}: {e}")


# How long a shard is kept, and how many may exist at once. Retention bounds the
# directory; it also bounds `suppressed`, which is a count over the window
# rather than over all time — see derive_health.
SHARD_MAX_AGE_DAYS = 30.0
SHARD_MAX_FILES = 400

# Rows kept in the rollup. Unchanged: the dashboard renders this list.
RECENT_LIMIT = 20


def _shard_dir():
    return DATA_DIR / "alerts" / "runs"


def _run_id() -> str:
    """Identifies the writer. One shard per run is what makes this collide-free.

    GITHUB_RUN_ID in CI; the pid locally, so two local processes still differ.
    """
    raw = os.environ.get("GITHUB_RUN_ID") or f"local-{os.getpid()}"
    return "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(raw))[:64]


def _shard_path():
    """This run's shard. Stable within a run, so repeat sends append to one file."""
    return _shard_dir() / f"{_run_id()}.json"


def _append_to_shard(event: dict) -> None:
    """Add one event to this run's own file. A run owns it, so nothing races us."""
    path = _shard_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    events = []
    if path.exists():
        try:
            with open(path) as f:
                events = (json.load(f) or {}).get("events") or []
        except (OSError, ValueError):
            events = []
    events.append(event)
    atomic_write_json(path, {"run_id": _run_id(), "events": events})


def load_shard_events() -> list[dict]:
    """Every event from every shard. An unreadable shard is skipped, not fatal."""
    out: list[dict] = []
    directory = _shard_dir()
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            with open(path) as f:
                out.extend((json.load(f) or {}).get("events") or [])
        except (OSError, ValueError):
            continue
    return out


def _migrate_legacy_record() -> None:
    """Carry a pre-shard latest.json into a shard once, so history is not lost.

    Keyed on there being NO shards yet, not on legacy.json being absent. Once
    sharding has started, latest.json is itself derived from the shards, so
    migrating it again would fold every event back in a second time — and
    `legacy.json` may by then have been pruned for age, which would let that
    happen repeatedly.
    """
    directory = _shard_dir()
    if directory.exists() and any(directory.glob("*.json")):
        return
    legacy = directory / "legacy.json"
    path = DATA_DIR / "alerts" / "latest.json"
    if not path.exists():
        return
    try:
        with open(path) as f:
            rows = (json.load(f) or {}).get("recent") or []
    except (OSError, ValueError):
        return
    if not rows:
        return
    legacy.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(legacy, {"run_id": "legacy", "events": rows})


def partition_shards_by_age(items, max_age_days: float,
                            max_files: int | None = None):
    """(name, age_days) pairs -> (kept, dropped). Youngest survive. Pure."""
    ordered = sorted(items, key=lambda it: it[1])
    kept = [it for it in ordered if it[1] <= max_age_days]
    if max_files is not None and len(kept) > max_files:
        kept = kept[:max_files]
    keep_names = {name for name, _age in kept}
    return kept, [it for it in ordered if it[0] not in keep_names]


def _prune_shards() -> None:
    """Bound the directory. Deleting a file never conflicts with another run."""
    directory = _shard_dir()
    if not directory.exists():
        return
    now = datetime.now(UTC).timestamp()
    items = []
    for path in directory.glob("*.json"):
        # `legacy.json` holds the pre-shard history and has no run to rewrite
        # it, so it ages out on mtime like any other shard.
        try:
            items.append((path.name, (now - path.stat().st_mtime) / 86400.0))
        except OSError:
            continue
    _kept, dropped = partition_shards_by_age(items, SHARD_MAX_AGE_DAYS,
                                             SHARD_MAX_FILES)
    for name, _age in dropped:
        try:
            (directory / name).unlink()
        except OSError:
            continue


def _event_sort_key(event: dict):
    try:
        return datetime.fromisoformat(str(event.get("at")))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)


def derive_health(events: list[dict]) -> dict:
    """The rollup, computed from scratch. Pure, so it cannot drift.

    Every field is a function of the events, which is the property that makes a
    lost rollup harmless: recomputing from the surviving shards gives the same
    answer. An event whose `status` is "suppressed" is counted and kept but
    never touches a health field — that is policy withholding an alert, not a
    channel failing. Note that a DELIVERED info is not suppressed and does
    count as a success, which is the behaviour the previous implementation had.

    `suppressed` counts the retention window rather than all time, because the
    shards are the only record and they are pruned at 30 days.
    """
    ordered = sorted(events or [], key=_event_sort_key)
    state = {
        "updated_at": datetime.now(UTC).isoformat(),
        "healthy": True,
        "consecutive_failures": 0,
        "undelivered": 0,
        "suppressed": 0,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "recent": ordered[-RECENT_LIMIT:],
    }
    for event in ordered:
        status = event.get("status")
        if status is None:
            status = ("delivered" if event.get("delivered") else
                      ("suppressed" if not _health_bearing(
                          str(event.get("subject") or "")) else "failed"))
        if status == "suppressed":
            state["suppressed"] += 1
            continue
        delivered = status == "delivered"
        state["healthy"] = delivered
        state["consecutive_failures"] = (
            0 if delivered else state["consecutive_failures"] + 1)
        state["undelivered"] = 0 if delivered else state["undelivered"] + 1
        if delivered:
            state["last_success_at"] = event.get("at")
        else:
            state["last_failure_at"] = event.get("at")
            state["last_failure_reason"] = event.get("reason")
    return state


def _cooldown_ok(key: str, hours: float) -> bool:
    """Rate-limit repeat alerts. Cursor is only written after a successful send."""
    last = read_cursor(f"alert_{key}")
    return not last or (now_ms() - last) >= hours * 3600 * 1000


# A second channel that needs no credential the operator has to activate. The
# workflows already hold `issues: write` and Actions already injects a token, so
# this works with nothing new configured — which is the point: email delivery has
# never once succeeded on this deployment (last_success_at was null across 1,947
# consecutive failures, Brevo answering "your SMTP account is not yet activated"),
# and a detector that cannot reach its operator is not a detector.
#
# Severities worth waking someone for. INFO alerts stay email-only: there are
# hundreds of them and they would bury the two that matter.
ESCALATING_SEVERITIES = ("CRITICAL", "HIGH")

# Per run, across every call. The backlog is in the thousands; without this an
# outage that clears would open an issue for every one of them.
MAX_ISSUES_PER_RUN = 3
_issues_opened_this_run = 0


def _severity_of(subject: str) -> str:
    """The severity the subject was built with, or "" if it carries none."""
    for level in ("CRITICAL", "HIGH", "INFO"):
        if f"] {level}:" in subject:
            return level
    return ""


def _health_bearing(subject: str) -> bool:
    """Whether failing to deliver this alert says anything about channel health.

    INFO is deliberately routed nowhere that buzzes — `_send_webhooks` gates on
    severity, and so does the GitHub fallback — so an INFO alert that no channel
    took is this system's own policy working, not an outage. Counting it as a
    delivery failure is what pinned `healthy` to false all through 2026-09-11
    while every CRITICAL that afternoon arrived on ntfy within seconds, and a
    flag that is always false cannot report the outage it exists for.

    Everything else is health-bearing, including a subject whose severity
    cannot be read: an alert we cannot classify must never be assumed harmless.
    `NTFY_INCLUDE_INFO` puts INFO back on the instant channels, and an alert
    that is meant to arrive is one whose failure to arrive counts again.
    """
    if _severity_of(subject) != "INFO":
        return True
    return bool(os.environ.get("NTFY_INCLUDE_INFO"))


def discovery_severity(classification: str) -> str:
    """The severity a transfer-graph discovery of this class alerts at."""
    return "CRITICAL" if classification == "MIGRATION_CANDIDATE" else (
        "HIGH" if classification == "POSSIBLE_LINKED_WALLET" else "INFO")


def discovery_withheld(classification: str) -> bool:
    """Whether such a discovery is withheld by policy rather than delivered.

    `fire_alerts` needs this to report what actually left the process, and it
    must not carry its own copy of the rule — asking here means `NTFY_INCLUDE_INFO`
    flows through in one place and the log cannot disagree with the channel.
    """
    return not _health_bearing(f"[EZEKIEL] {discovery_severity(classification)}: x")


def _github_issue_fallback(key: str, subject: str, body: str) -> bool:
    """Open a GitHub issue so a failed email still reaches the operator.

    Returns True only when an issue was actually created or already exists for
    this alert — either way the operator can see it, so the caller may treat it
    as delivered and start the cooldown.

    Silent no-op without a token (every local run), so this never becomes a
    reason a developer's run behaves differently from CI's.
    """
    global _issues_opened_this_run

    if _severity_of(subject) not in ESCALATING_SEVERITIES:
        return False
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return False
    if _issues_opened_this_run >= MAX_ISSUES_PER_RUN:
        print(f"[alerts] GitHub fallback cap reached ({MAX_ISSUES_PER_RUN} this "
              f"run), not opening an issue for: {subject}")
        return False

    import requests

    title = f"{subject} [{key}]"
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{repo}/issues"
    try:
        # An open issue for this exact alert already reaches the operator;
        # opening a second one every 30 minutes would not add information.
        existing = requests.get(api, headers=headers,
                                params={"state": "open", "per_page": 100},
                                timeout=20)
        if existing.status_code == 200:
            for issue in existing.json():
                if issue.get("title") == title:
                    print(f"[alerts] GitHub issue already open for {key}")
                    return True

        created = requests.post(
            api, headers=headers, timeout=20,
            json={"title": title,
                  "body": (f"{body}\n\n---\nRaised by Ezekiel because email "
                           f"delivery failed. Close this once actioned.")})
        if created.status_code in (200, 201):
            _issues_opened_this_run += 1
            print(f"[alerts] Email failed — raised GitHub issue instead: {subject}")
            _record_delivery(subject, True, "delivered via GitHub issue fallback")
            return True
        print(f"[alerts] GitHub fallback failed (HTTP {created.status_code})")
    except Exception as exc:                          # noqa: BLE001 - transport
        print(f"[alerts] GitHub fallback failed ({type(exc).__name__}): {exc}")
    return False


def _send_with_cooldown(key: str, hours: float, subject: str, body: str) -> bool:
    """True when the alert has been DISPOSED OF — delivered, or deliberately
    withheld. False only when something meant to arrive did not, which is the
    one case a caller should queue for retry.

    The distinction is the difference between a retry and a livelock. A failed
    send must consume no cooldown and stay queued, because the next run may
    succeed. An alert policy routes nowhere can never succeed on a retry, so
    treating it as pending re-fires it every run forever: measured 2026-09-11,
    one INFO notice came round seven times in five hours, each pass evicting a
    real row from the 20-entry delivery record while the wallet sat permanently
    in the transfer graph's `undelivered_alerts`.
    """
    if not _cooldown_ok(key, hours):
        print(f"[alerts] Cooldown active for {key}, skipping: {subject}")
        return False
    if send_alert(subject, body):
        write_cursor(f"alert_{key}", now_ms())
        return True
    # Email failed. For anything worth waking someone for, try the channel that
    # does not depend on a mail provider being activated.
    if _github_issue_fallback(key, subject, body):
        write_cursor(f"alert_{key}", now_ms())
        return True
    # Nothing carried it — but if nothing was ever going to, that is this
    # system's own policy working, and there is nothing for a retry to fix.
    # It is on the dashboard and counted in the delivery record; consume the
    # cooldown so it does not come round again.
    if not _health_bearing(subject):
        print(f"[alerts] Withheld by policy (not routed at this severity): {subject}")
        write_cursor(f"alert_{key}", now_ms())
        return True
    return False


def _send_email(subject: str, body: str, html_body: str | None = None) -> tuple[bool, str | None]:
    """Send by SMTP. Returns (delivered, reason) and records nothing itself."""
    global _smtp_disabled_this_run

    if _smtp_disabled_this_run:
        print(f"[alerts] SMTP disabled after earlier failure this run, skipping: {subject}")
        return False, "skipped: SMTP disabled after an earlier failure this run"

    smtp_login = os.environ.get("BREVO_SMTP_LOGIN")
    smtp_key = os.environ.get("BREVO_SMTP_KEY")
    alert_email = os.environ.get("ALERT_EMAIL")

    # Name what is missing, never its value.
    missing = [name for name, value in (
        ("BREVO_SMTP_LOGIN", smtp_login),
        ("BREVO_SMTP_KEY", smtp_key),
        ("ALERT_EMAIL", alert_email),
    ) if not value]
    if missing:
        print(f"[alerts] SMTP not configured — missing: {', '.join(missing)}. "
              f"Alert not sent: {subject}")
        if "BREVO_SMTP_LOGIN" in missing:
            print("[alerts] BREVO_SMTP_LOGIN is the SMTP login from Brevo "
                  "Settings -> SMTP & API (<id>@smtp-brevo.com), not the account "
                  "email and not an API key.")
        print(f"[alerts] {body[:200]}")
        # Unconfigured is a delivery outage too. Name the variables, never values.
        return False, f"not configured: missing {', '.join(missing)}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Ezekiel Alerts <{alert_email}>"
    msg["To"] = alert_email

    msg.attach(MIMEText(body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            # Username is the Brevo SMTP login, password is the SMTP key. Passing
            # the literal "apikey" here (SendGrid's convention) is what produced
            # 535 5.7.8 Authentication failed on every send.
            server.login(smtp_login, smtp_key)
            server.sendmail(msg["From"], [alert_email], msg.as_string())
        print(f"[alerts] Sent: {subject}")
        return True, None
    except smtplib.SMTPAuthenticationError as e:
        # Credentials were rejected. Report the server's code and the variable
        # names involved — never the values.
        print(f"[alerts] SMTP authentication REJECTED by {SMTP_HOST} "
              f"(code {e.smtp_code}). Check that BREVO_SMTP_LOGIN is the SMTP "
              f"login from Brevo Settings -> SMTP & API (<id>@smtp-brevo.com) and "
              f"that BREVO_SMTP_KEY is an SMTP key from that same page — the "
              f"account password and v3 API keys are not accepted.")
        _smtp_disabled_this_run = True
        print("[alerts] Disabling further sends for this run.")
        return False, (f"SMTP authentication rejected (code {e.smtp_code}) - check "
                       f"BREVO_SMTP_LOGIN and BREVO_SMTP_KEY")
    except smtplib.SMTPSenderRefused as e:
        print(f"[alerts] Sender address refused (code {e.smtp_code}). ALERT_EMAIL "
              f"must be a verified sender on the Brevo account.")
        _smtp_disabled_this_run = True
        print("[alerts] Disabling further sends for this run.")
        return False, (f"sender address refused (code {e.smtp_code}) - ALERT_EMAIL must be "
                       f"a verified sender on the Brevo account")
    except Exception as e:
        print(f"[alerts] Failed to send ({type(e).__name__}): {e}")
        _smtp_disabled_this_run = True
        print("[alerts] Disabling further sends for this run.")
        return False, f"{type(e).__name__}: {e}"


def _send_webhooks(subject: str, body: str) -> list[str]:
    """Deliver through the instant channels, if configured. Returns the ones that took it.

    Email through Brevo has never once delivered on this deployment (the
    account was never activated), and the GitHub-issue fallback carries only
    CRITICAL and HIGH. A Telegram bot or an ntfy topic is free, needs no
    activation, and arrives in seconds. Configure either or both:

        TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID   a bot token from @BotFather and
                                                 the chat it should post to
        NTFY_TOPIC                               a topic name on ntfy.sh

    Never raises: a dead channel is reported, and the others still run.
    """
    delivered: list[str] = []
    text = subject + "\n\n" + body
    severity = _severity_of(subject)
    # Severity-gated for the same reason the GitHub fallback is: there are
    # hundreds of INFO alerts and two that matter. Measured 2026-09-11, the
    # first trace run after this channel went live pushed 24 "Operational
    # Counterparty" notices at 3-11% confidence to a phone in one minute,
    # which is how an operator learns to swipe the channel away. INFO is still
    # written to disk, still on the dashboard, and still in the delivery
    # record; it just does not buzz. Set NTFY_INCLUDE_INFO=1 to opt back in.
    if severity not in ESCALATING_SEVERITIES and not os.environ.get("NTFY_INCLUDE_INFO"):
        return delivered
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            import requests
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": chat, "text": text[:4000],
                                    "disable_web_page_preview": True}, timeout=20)
            if r.status_code == 200:
                delivered.append("telegram")
            else:
                print(f"[alerts] Telegram rejected the message (HTTP {r.status_code})")
        except Exception as exc:                      # noqa: BLE001 - transport
            print(f"[alerts] Telegram send failed ({type(exc).__name__}): {exc}")
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            import requests
            title = subject.encode("ascii", "ignore").decode()
            r = requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                              headers={"Title": title,
                                       "Priority": "high" if severity in ESCALATING_SEVERITIES
                                       else "default"}, timeout=20)
            if r.status_code == 200:
                delivered.append("ntfy")
            else:
                print(f"[alerts] ntfy rejected the message (HTTP {r.status_code})")
        except Exception as exc:                      # noqa: BLE001 - transport
            print(f"[alerts] ntfy send failed ({type(exc).__name__}): {exc}")
    return delivered


def send_alert(subject: str, body: str, html_body: str | None = None) -> bool:
    """Deliver an alert on every configured channel. True if any took it.

    Webhooks first (instant, never needed activation), then email. Delivery
    on any channel counts: an operator who reads Telegram must not have the
    alert marked undelivered because Brevo is still unactivated. Health is
    recorded once, naming the channel that delivered and the email reason
    when it did not.
    """
    via = _send_webhooks(subject, body)
    smtp_ok, reason = _send_email(subject, body, html_body)
    if via:
        note = f"delivered via {', '.join(via)}"
        if not smtp_ok and reason:
            note += f"; email: {reason}"
        _record_delivery(subject, True, note)
        return True
    _record_delivery(subject, smtp_ok, reason)
    return smtp_ok


def alert_fund_movement(wallet: str, amount: str, destination: str, tx_hash: str,
                        asset: str = "USDC", chain: str = "arbitrum",
                        occurred_at: str | None = None) -> bool:
    """`asset`/`chain` default to the pre-substrate behavior (this was always
    Arbitrum USDC) so this stays correct for a caller that doesn't pass them.
    The caller now sourced from every chain and asset must pass the real
    ones — reporting "USDC" for a USDT withdrawal would be a false statement
    in the one artifact this system exists to produce.

    `amount` is expected to already be a dollar-qualified string (e.g.
    "$2,000,000.00"), not a bare number — this function does not add its own
    currency symbol. That figure is always a USD value, never a token
    quantity, even when `asset` is not a dollar-pegged stablecoin: reporting
    "Withdrawal of 2,000,000.00 ETH" would say the trader moved a thousand
    times more ETH than they did the moment a non-stablecoin's price is
    actually looked up, so the wording spells out "of <asset>" rather than
    letting the number and the asset run together.
    """
    when = (f"When: {occurred_at}\n" if occurred_at
            else "When: unknown - this may be a historical transfer surfacing now\n")
    subject = f"[EZEKIEL] CRITICAL: Fund Movement Detected ({amount} of {asset} on {chain})"
    body = (
        f"{address_line(wallet, 'Wallet')}\n"
        f"Event: Withdrew {amount} of {asset} on {chain}\n"
        # WHEN the money moved, not when we noticed. An alert omitting it
        # reads as breaking news regardless of age: this fired for a transfer
        # from 2025-10-17 and the first person to read it took it for current
        # activity. A cursor advancing, a queued alert finally delivering, or
        # a newly swept wallet all surface old transfers.
        f"{when}"
        f"{address_line(destination, 'Destination')}\n"
        f"TX Hash: {tx_hash}\n"
        f"\nTracing destination wallet..."
    )
    return _send_with_cooldown(f"fund_movement_{tx_hash.lower()}", 24, subject, body)


def alert_new_wallet_found(source_wallet: str, new_wallet: str, method: str, confidence: float) -> bool:
    subject = f"[EZEKIEL] {'CRITICAL' if method == 'fund_trace' else 'HIGH'}: New Linked Wallet Detected"
    body = (
        f"{address_line(new_wallet, 'New Wallet')}\n"
        f"Detection Method: {method}\n"
        f"Confidence: {confidence:.0%}\n"
        f"{address_line(source_wallet, 'Source Wallet')}\n"
    )
    return _send_with_cooldown(f"new_wallet_{new_wallet.lower()}", 72, subject, body)


def alert_behavioral_match(candidate: str, score: float, dimensions: dict) -> bool:
    subject = f"[EZEKIEL] HIGH: Behavioral Match ({score:.0%} similarity)"
    # Dimensions excluded for insufficient data are None — sort and format would
    # both raise on them, so they are listed separately rather than dropped.
    scored = {k: v for k, v in dimensions.items() if isinstance(v, (int, float))}
    skipped = [k for k, v in dimensions.items() if not isinstance(v, (int, float))]
    dim_lines = "\n".join(
        f"  - {k}: {v:.2f}" for k, v in sorted(scored.items(), key=lambda x: -x[1])
    )
    if skipped:
        dim_lines += f"\n  (not comparable, excluded: {', '.join(sorted(skipped))})"
    body = (
        f"{address_line(candidate, 'Candidate Wallet')}\n"
        f"Similarity Score: {score:.2f} / 1.00\n\n"
        f"Matching Dimensions:\n{dim_lines}\n"
    )
    return _send_with_cooldown(f"behavioral_{candidate.lower()}", 24, subject, body)


def alert_combined_match(candidate: str, score: float, flow_amount: str, flow_method: str) -> bool:
    """Fire when the same wallet appears in both fund-flow tracing AND behavioral matching."""
    subject = "[EZEKIEL] CRITICAL: Fund Trace + Behavioral Match on Same Wallet"
    body = (
        f"HIGHEST CONFIDENCE SIGNAL — BOTH VECTORS POINT TO SAME WALLET\n\n"
        f"{address_line(candidate, 'Candidate Wallet')}\n"
        f"Behavioral Similarity: {score:.2f} / 1.00\n"
        f"Fund Flow: {flow_amount} USDC via {flow_method}\n\n"
        f"This wallet received funds from the target AND matches the behavioral fingerprint.\n"
        f"Recommended action: begin monitoring this wallet immediately.\n"
    )
    return _send_with_cooldown(f"combined_{candidate.lower()}", 12, subject, body)


def alert_target_silence(days_silent: float) -> bool:
    subject = f"[EZEKIEL] WARNING: Target Wallet Silent for {days_silent:.1f} Days"
    body = (
        f"The target wallet has made NO fills for {days_silent:.1f} days.\n\n"
        f"This may indicate migration to a new wallet.\n"
        f"Action: check Recovery page for behavioral candidates and fund flow activity.\n"
    )
    return send_alert(subject, body)


def alert_migration_correlation(candidate: str, score: float, days_silent: float) -> bool:
    subject = "[EZEKIEL] CRITICAL: Migration Correlation — Target Silent + New Candidate"
    body = (
        f"HIGH CONFIDENCE MIGRATION SIGNAL\n\n"
        f"Target wallet has been silent for {days_silent:.1f} days\n"
        f"AND a new behavioral candidate appeared in the same window:\n\n"
        f"{address_line(candidate, 'Candidate')}\n"
        f"Behavioral Match: {score:.2f} / 1.00\n\n"
        f"These two signals together are the strongest possible migration indicator.\n"
        f"Recommended action: begin monitoring candidate wallet immediately.\n"
    )
    return _send_with_cooldown(f"migration_{candidate.lower()}", 24, subject, body)


def alert_vault_match(candidate: str, shared_vaults: list) -> bool:
    subject = "[EZEKIEL] HIGH: Vault Overlap — Candidate Uses Same HL Vault as Target"
    vault_lines = "\n".join(f"  - {v}" for v in shared_vaults[:5])
    body = (
        f"Candidate wallet deposits to the same Hyperliquid vault(s) as the target.\n"
        f"This is a strong behavioral link — vault addresses are not widely shared.\n\n"
        f"{address_line(candidate, 'Candidate')}\n"
        f"Shared Vaults:\n{vault_lines}\n"
    )
    return _send_with_cooldown(f"vault_{candidate.lower()}", 72, subject, body)


def alert_hl_native_transfer(destination: str, out_usd: float, in_usd: float,
                             bidirectional: bool, tokens: list) -> bool:
    """Fire when the target sends significant funds to a wallet ENTIRELY within
    Hyperliquid (no L1 footprint). This is the most likely migration path and is
    invisible to the L1 tracer."""
    subject = "[EZEKIEL] CRITICAL: HL-Native Transfer to New Wallet"
    token_str = ", ".join(tokens[:5]) if tokens else "USDC"
    body = (
        f"The target moved funds to another wallet ENTIRELY INSIDE Hyperliquid.\n"
        f"This leaves no Arbitrum L1 trace — it is the most likely migration path.\n\n"
        f"{address_line(destination, 'Destination')}\n"
        f"Sent to this wallet: ${out_usd:,.2f}\n"
        f"Received from this wallet: ${in_usd:,.2f}\n"
        f"Two-way relationship: {'YES — very likely same owner' if bidirectional else 'no'}\n"
        f"Tokens: {token_str}\n\n"
        f"Action: this wallet is now a top migration candidate. Check the Recovery "
        f"page — it is being behaviorally scanned automatically.\n"
    )
    return _send_with_cooldown(f"hl_transfer_{destination.lower()}", 72, subject, body)


def alert_discovery_stalled(hours: float, last_expansion_at: str | None,
                            status: str | None, error: str | None,
                            queued: int, top_queued: str | None) -> bool:
    """Fire when the transfer graph's frontier has stopped finding new addresses.

    The frontier is the only vector that can reach an address nobody has seen.
    Every other vector needs something already known: a counterparty to follow,
    an amount to match, a fingerprint to compare against. When the walk stops
    the graph still rebuilds from known edges, the roster still tiers, and every
    report still looks healthy — so the failure presents as an absence of
    discoveries, which is indistinguishable from there being nothing to find.

    Not hypothetical. From 2026-09-09 18:56 to 2026-09-11 the walk explored zero
    wallets on every run, deferring each one over three chains Etherscan's free
    tier refuses outright, and nothing said so. `0xf078969e...` — CONFIRMED,
    two-way with the target — sat at the head of the queue the whole time.

    HIGH rather than CRITICAL by design: CRITICAL in this system means something
    about the TARGET — he moved, a wallet is his. This is a capability of ours
    being down. Filing it as CRITICAL would teach the operator to discount the
    severity that matters most.
    """
    subject = f"[EZEKIEL] HIGH: Wallet Discovery Stalled ({hours:.1f}h)"
    last = last_expansion_at or "never — no expansion on record"
    if top_queued:
        cost = (f"  {queued} wallet(s) queued behind the stall. Highest priority:\n"
                f"    {top_queued}\n")
    else:
        cost = f"  {queued} wallet(s) queued behind the stall.\n"
    body = f"""The transfer graph's L1 frontier has not expanded a single wallet in
{hours:.1f} hours.

  last expansion  : {last}
  last run status : {status or "unknown"}
  reason          : {error or "(none recorded)"}

WHAT IT IS COSTING
{cost}
WHY THIS MATTERS
  The frontier is the only vector that reaches an address nobody has seen.
  Everything else starts from something we already have. While it is stalled
  the system cannot find a wallet he funded from somewhere unobserved, and the
  failure looks exactly like there being nothing to find.

WHERE TO LOOK
  data/transfer_graph/latest.json -> health.expansion
  `status`, `partial_failures` and `degraded_sources` name the chains that
  could not be read. `unsupported_sources` is NOT a fault: those chains are
  off our API plan permanently and are expected to be listed on every run.
"""
    return _send_with_cooldown("discovery_stalled", 24, subject, body)


def alert_scorer_unreliable(self_score: float, best_stranger: float,
                            margin: float, rank: int, total: int,
                            failures: list) -> bool:
    """Fire when the self-match backtest says the scorer cannot identify the target.

    The backtest scores the trader's own recent window against his own fingerprint
    and ranks it among strangers. If he does not come first by a clear margin,
    then "trades like the target" does not mean what the rest of the system
    assumes, and every behavioural score is noise dressed as evidence.

    This has to be loud because its consequence is silence: with no validated
    ceiling the thresholds stay at conservative config values the scorer cannot
    reach, so behavioural corroboration contributes nothing and the failure shows
    up as an absence of alerts rather than as a problem.
    """
    subject = "[EZEKIEL] CRITICAL: Behavioural Scorer Cannot Identify The Target"
    reasons = "\n".join(f"  - {f}" for f in failures) or "  - (none recorded)"
    body = f"""The self-match backtest FAILED. The target's own recent trading was
scored against his own fingerprint and did not stand out from strangers.

  self-match score : {self_score:.4f}
  best stranger    : {best_stranger:.4f}
  margin           : {margin:+.4f}  (needs >= +0.05)
  rank             : {rank} of {total} strangers  (needs 1)

WHY IT FAILED
{reasons}

WHAT THIS MEANS
  Behavioural similarity is not currently reliable evidence of identity for
  this trader. Thresholds stay in OBSERVING, so a behavioural score
  contributes nothing to migration confidence and cannot promote a wallet on
  its own. Fund-flow, HL-native transfers, amount correlation and address
  reuse are unaffected and remain the vectors worth acting on.

  Do NOT fix this by lowering thresholds or reweighting dimensions until it
  passes: that fits the one measurement that validates the scorer, and would
  turn an honest negative into false confidence.
"""
    return _send_with_cooldown("scorer_unreliable", 168, subject, body)


def alert_target_dormant(silent_days: int, stats: dict, unprecedented: bool) -> bool:
    """Fire when the target is quiet for longer than his own history allows.

    "Dormant" means nothing in the abstract. This trader's median gap between
    active days is 2 and he has taken 21 off before, so a fixed "silent for a
    week" rule would cry wolf constantly. The comparison is against HIS
    distribution.

    It matters because a silence is the one migration signal that needs no
    connection at all: if he abandons this wallet and funds a fresh one from
    somewhere unobservable, the alignment of his silence with another wallet's
    birth is the only thing left to find him by.
    """
    level = "unprecedented" if unprecedented else "unusual"
    subject = f"[EZEKIEL] CRITICAL: Target Silent {silent_days} Days ({level})"
    verdict = (
        "This is LONGER THAN ANY SILENCE HE HAS EVER TAKEN. That is a change of\n"
        "behaviour, not a quiet week."
        if unprecedented else
        "This is longer than usual for him, but not unprecedented."
    )
    body = f"""The target has not traded for {silent_days} day(s).

HIS OWN RHYTHM
  median gap between active days : {stats.get('median')}
  90th percentile                : {stats.get('p90')}
  longest silence ever recorded  : {stats.get('max')}
  active days observed           : {stats.get('active_days')}

{verdict}

WHY IT MATTERS
  A wallet going quiet while another comes alive is the clearest migration
  signature there is, and the only one needing no transfer, shared address or
  authorised agent to connect them.

Action: look for wallets that STARTED trading in the last few days.
"""
    # Keyed in 3-day bands so a lengthening silence re-alerts as it worsens,
    # rather than once and then never again.
    return _send_with_cooldown(f"dormant_{silent_days // 3}", 48, subject, body)


def alert_dormancy_handoff(wallet: str, detail: dict) -> bool:
    """Fire when a wallet's first-ever activity lands inside a target silence."""
    subject = "[EZEKIEL] CRITICAL: Dormancy Handoff - Wallet Born During Target Silence"
    body = f"""A wallet began trading while the target was unusually quiet.

{address_line(wallet, 'Wallet')}
Its first activity came {detail.get('delay_days')} day(s) into a {detail.get('gap_length')}-day silence.
Handoff score: {detail.get('score')}

This needs no transfer between them, which is exactly why it is worth having:
it is the one signal that survives funding a fresh wallet from somewhere
unobservable.

Action: check whether it trades like him, and whether it shares any deposit
address.
"""
    return _send_with_cooldown(f"handoff_{wallet.lower()}", 168, subject, body)



def alert_shared_agent(agent: str, accounts: list) -> bool:
    """Fire when two accounts authorise the same Hyperliquid agent.

    An agent is an address an account EXPLICITLY approved to trade on its
    behalf. A transfer can be a payment to a stranger and an amount match can be
    coincidence, but authorising an agent is a deliberate act of control - so two
    accounts sharing one are operated by the same person. This is the strongest
    single signal this system can produce.
    """
    subject = "[EZEKIEL] CRITICAL: Shared Agent - Accounts Under Common Control"
    listed = chr(10).join(address_line(a, "Account") for a in accounts)
    body = (
        f"Two or more Hyperliquid accounts have authorised the SAME agent "
        f"address.{chr(10)}{chr(10)}"
        f"{address_line(agent, 'Agent')}{chr(10)}{chr(10)}"
        f"AUTHORISED BY{chr(10)}{listed}{chr(10)}{chr(10)}"
        f"An agent is an address an account explicitly approved to trade on its "
        f"behalf.{chr(10)}"
        f"Sharing one is a deliberate act of control by the same operator, not a "
        f"coincidence{chr(10)}of flow or of style.{chr(10)}{chr(10)}"
        f"Action: treat these accounts as the same person unless there is a "
        f"specific reason not to.{chr(10)}"
    )
    return _send_with_cooldown(f"shared_agent_{agent.lower()}", 168, subject, body)


def alert_explicit_link(kind: str, address: str, linked_to: str, why: str) -> bool:
    """Fire when Hyperliquid itself declares two addresses under one control.

    `userRole` answers that an address is somebody's AGENT or SUB-ACCOUNT, and
    `userFees.stakingLink` declares a staking wallet paired with a trading
    wallet. Each is an act the account owner performed, not an inference from
    flow or style, so each is strong enough to confirm on its own.
    """
    subject = f"[EZEKIEL] CRITICAL: Explicit Hyperliquid Link ({kind})"
    body = (
        f"Hyperliquid reports a deliberate link between two addresses.\n\n"
        f"{address_line(address, 'Address')}\n"
        f"{address_line(linked_to, 'Linked to')}\n"
        f"Kind: {kind}\n"
        f"Why: {why}\n\n"
        f"An agent, a sub-account or a staking link is set up by the account\n"
        f"owner. Treat both addresses as the same person unless there is a\n"
        f"specific reason not to.\n"
    )
    return _send_with_cooldown(f"explicit_{kind}_{address.lower()}", 168, subject, body)


def alert_foreign_destination(wallet: str, kind: str, destination: str,
                              amount, token: str | None, when: str | None,
                              tx_hash: str | None = None,
                              chain: str | None = None) -> bool:
    """Fire when a cluster wallet hands value or control to an address outside
    the cluster, as seen in its own Hyperliquid actions.

    `chain` is set for a `sendToEvmWithData` — Hyperliquid's native Circle
    withdrawal, which names a recipient on ANY CCTP chain. The ledger shows
    it only as a send to `0x2000…0000`, so this alert is the only place the
    operator learns which chain to look at.

    The info API's ledger cannot show where a withdrawal went or which agent
    was approved; the explorer's action record can. A `withdraw3` to a fresh
    Arbitrum address, a `usdSend` to an unknown account, a new agent or a new
    sub-account are each the first observable step of a migration.
    """
    if kind == "approveAgent":
        subject = "[EZEKIEL] HIGH: New Agent Approved by a Cluster Wallet"
        lead = ("A cluster wallet approved a new agent — a fresh address that now\n"
                "signs for the account. Any other account approving the same one is\n"
                "the same person.\n\n")
    elif kind == "sendToEvmWithData":
        subject = (f"[EZEKIEL] CRITICAL: Circle/CCTP withdrawal to an address outside "
                   f"the cluster on {chain or 'an unknown chain'}")
        lead = ("A cluster wallet withdrew through Hyperliquid's native Circle route\n"
                "(`sendToEvmWithData`) to a recipient the roster does not know as his.\n"
                "This route bypasses the Arbitrum bridge entirely: the money is minted\n"
                "as USDC at the recipient on the named chain within minutes, and no\n"
                "L1 sweep of his own addresses will ever see it.\n\n")
    else:
        subject = f"[EZEKIEL] CRITICAL: {kind} to an address outside the cluster"
        lead = (f"A cluster wallet performed `{kind}` towards an address the roster does\n"
                f"not know as his.\n\n")
    body = (
        lead
        + f"{address_line(wallet, 'Wallet')}\n"
        f"{address_line(destination, 'Destination')}\n"
        + (f"Chain: {chain}\n" if chain else "")
        + f"Amount: {amount if amount is not None else 'n/a'} {token or ''}\n"
        f"When: {when or 'unknown'}\n"
        f"Hyperliquid tx: {tx_hash or 'n/a'}\n\n"
        f"This is the account's OWN action, read from the Hyperliquid explorer,\n"
        f"not an inference from flow or style. Check the destination on\n"
        f"Hyperliquid at once: an account funded this way is a new wallet he\n"
        f"controls until shown otherwise.\n"
    )
    key = f"foreign_{kind}_{(destination or '').lower()}"
    return _send_with_cooldown(key, 72, subject, body)


def alert_unresolved_cctp_withdrawal(wallet: str, net_usd, when: str | None,
                                     tx_hash: str | None) -> bool:
    """A Circle withdrawal whose recipient nobody can name any more.

    The ledger shows a send to the USDC system address; the explorer payload
    that carried the recipient has rolled out of its 300-action window; and
    no mint of the amount arrived at any cluster address on a chain the
    substrate reads. That is money leaving Hyperliquid to an address this
    project cannot see — not a contact, so HIGH rather than CRITICAL, but
    never silence. Reported once per withdrawal.
    """
    subject = "[EZEKIEL] HIGH: Circle withdrawal to an address nobody can name"
    body = (
        "A cluster wallet withdrew through Hyperliquid's native Circle route\n"
        "(`sendToEvmWithData`), and the recipient cannot be recovered: the\n"
        "explorer payload has rolled out of its window and no USDC mint of this\n"
        "amount landed at any address the roster knows as his.\n\n"
        f"{address_line(wallet, 'Wallet')}\n"
        f"Amount: {net_usd if net_usd is not None else 'n/a'} USDC\n"
        f"When: {when or 'unknown'}\n"
        f"Hyperliquid tx: {tx_hash or 'n/a'}\n\n"
        "Look for a fresh USDC mint of this amount on the CCTP chains around\n"
        "that time. An account funded this way is a new wallet he controls\n"
        "until shown otherwise.\n"
    )
    key = f"cctp_unresolved_{(tx_hash or when or '').lower()}"
    return _send_with_cooldown(key, 24 * 14, subject, body)


def alert_newborn_whale(wallet: str, account_value: float, age: str,
                        volume: float, display_name: str | None) -> bool:
    """Fire when a large account that did not exist a week ago appears.

    The behavioural sweep scans the 500 largest accounts, so a migrated wallet
    is invisible to it until it has grown. Birth, read from the leaderboard's
    own window volumes, is orthogonal to size and is the one selection that
    catches a fresh wallet early.
    """
    subject = f"[EZEKIEL] HIGH: Newborn Account Holding ${account_value:,.0f}"
    body = (
        f"An account born within the last {age} already holds a large balance.\n\n"
        f"{address_line(wallet, 'Wallet')}\n"
        f"Account value: ${account_value:,.2f}\n"
        f"All-time volume: ${volume:,.0f} (all of it inside the {age})\n"
        f"Display name: {display_name or '(none)'}\n\n"
        f"It is being scanned behaviourally as a priority target. Birth alone\n"
        f"is not evidence of anything; check the roster for corroboration and\n"
        f"whether the target has been quiet.\n"
    )
    return _send_with_cooldown(f"newborn_{wallet.lower()}", 168, subject, body)


def alert_solana_activity(address: str, signatures: list, last_activity: str | None) -> bool:
    """Fire when a cluster Solana address moves."""
    subject = "[EZEKIEL] HIGH: Cluster Solana Address Active"
    sigs = "\n".join(f"  - {s}" for s in signatures[:10]) or "  (none listed)"
    body = (
        f"A Solana address tied to the cluster by decoded bridge calldata has new\n"
        f"transactions.\n\n"
        f"Address: {address}\n"
        f"Latest activity: {last_activity or 'unknown'}\n"
        f"New signatures:\n{sigs}\n\n"
        f"Solana reaches Hyperliquid through Circle CCTP (domain 19). Check\n"
        f"whether any of these is a CCTP burn and, if so, which Hyperliquid\n"
        f"account the hook data names.\n"
    )
    return _send_with_cooldown(f"solana_{address}", 24, subject, body)


def alert_watchlist_contact(wallet: str, contact: str, what: str,
                            why: str | None = None,
                            severity: str = "CRITICAL") -> bool:
    """Fire when a wallet under close watch touches the target's world.

    The watched wallet is there because two inferences agreed about it — an
    amount coincidence and a birth inside his silence. This is not an
    inference: it is a transfer or an action that happened, between it and
    him, a wallet believed to be his, or one of his private deposit
    addresses. That is the third vector, and the one that settles it.
    """
    # A roster tier is an inference this system made; his own addresses are
    # ground truth or measurement. Only the second kind is worth a CRITICAL.
    subject = f"[EZEKIEL] {severity}: Watched Wallet Touched The Target's World"
    body = (
        f"{address_line(wallet, 'Watched wallet')}\n"
        f"{address_line(contact, 'Contact')}\n"
        f"What the contact is: {what}\n"
        f"Why this wallet is watched: {why or '(not recorded)'}\n\n"
        f"This is an observed connection, not a coincidence of amount or of\n"
        f"timing. Verify it by hand — a transfer is still not ownership — and\n"
        f"if it holds, this wallet moves from a question to a finding.\n"
    )
    return _send_with_cooldown(f"watch_contact_{wallet.lower()}_{contact.lower()}",
                               168, subject, body)


def alert_watchlist_change(wallet: str, changes: list, why: str | None = None) -> bool:
    """Fire when a watched wallet's state changes materially."""
    kinds = ", ".join(sorted({c.get("kind", "?") for c in changes}))
    subject = f"[EZEKIEL] HIGH: Watched Wallet Changed ({kinds})"
    lines = "\n".join(f"  - {c.get('kind')}: {c.get('detail')}" for c in changes)
    body = (
        f"{address_line(wallet, 'Watched wallet')}\n"
        f"Why it is watched: {why or '(not recorded)'}\n\n"
        f"WHAT CHANGED\n{lines}\n\n"
        f"A wallet emptying or going quiet is how a migration continues past\n"
        f"the wallet you are watching; a new agent or sub-account is a new\n"
        f"address it controls; a new withdrawal destination is an address to\n"
        f"compare against his own.\n"
    )
    # Keyed on the kinds, so a different change re-alerts and the same one does
    # not repeat every half hour.
    return _send_with_cooldown(f"watch_change_{wallet.lower()}_{kinds}", 24, subject, body)


def alert_same_hand(wallet: str, result: dict) -> bool:
    """Fire when a wallet's decisions lead or tie the target's too often to be a copier."""
    subject = "[EZEKIEL] HIGH: Wallet Moves With or Before the Target (not a copier)"
    body = (
        f"{address_line(wallet, 'Wallet')}\n"
        f"Paired decisions: {result.get('pairs')} (excess over a day-shifted control "
        f"{result.get('excess'):+.2f})\n"
        f"Leads or ties the target: {result.get('lead_share'):.0%}\n"
        f"Median lag: {result.get('median_lag_min')} min\n"
        f"Coins: {', '.join(result.get('coins') or [])[:200]}\n\n"
        f"A copy-trader reacts after the target's fills reach the tape. A wallet\n"
        f"that moves first, this often, is either the same hand or shares his\n"
        f"signal source. Corroborate with flow, a shared deposit address, an\n"
        f"agent or an explicit link before concluding.\n"
    )
    return _send_with_cooldown(f"same_hand_{wallet.lower()}", 72, subject, body)


def alert_vault_led(vault: str, leader: str, name: str | None, tvl) -> bool:
    subject = "[EZEKIEL] HIGH: A Cluster Wallet Leads a Hyperliquid Vault"
    body = (
        f"{address_line(vault, 'Vault')}\n"
        f"{address_line(leader, 'Leader')}\n"
        f"Name: {name or '(none)'}\nTVL: {tvl}\n\n"
        f"A vault he leads is a tradeable address of his that never appears as a\n"
        f"counterparty in fills or ledgers.\n"
    )
    return _send_with_cooldown(f"vault_led_{vault.lower()}", 168, subject, body)


def alert_name_hit(address: str, name: str, source: str) -> bool:
    subject = "[EZEKIEL] HIGH: Naming-Family Match on Hyperliquid"
    body = (
        f"A human-chosen {source} name matches the target's known naming family.\n\n"
        f"{address_line(address, 'Address')}\nName: {name}\n\n"
        f"A name is a reason to look, never a conclusion: check the roster and\n"
        f"the identity report for this address.\n"
    )
    return _send_with_cooldown(f"name_{address.lower()}", 168, subject, body)


def alert_target_gained_agent(agent: str, name: str | None) -> bool:
    """Fire when the target authorises an agent he did not have before.

    An agent is a new address he controls. Measured 2026-09-10 he had none, so
    the first one appearing is worth knowing about immediately - it is both a new
    address to watch and a possible precursor to moving accounts.
    """
    subject = "[EZEKIEL] CRITICAL: Target Authorised A New Agent Wallet"
    body = (
        f"The target has authorised an agent address he did not have before."
        f"{chr(10)}{chr(10)}"
        f"{address_line(agent, 'Agent')}{chr(10)}"
        f"Label: {name or '(none)'}{chr(10)}{chr(10)}"
        f"An agent trades on the account's behalf, so this is a NEW address under "
        f"his control.{chr(10)}"
        f"If any other account authorises the same agent, those accounts are the "
        f"same person.{chr(10)}"
    )
    return _send_with_cooldown(f"target_agent_{agent.lower()}", 168, subject, body)



def alert_hyperevm_activation(wallet: str, label: str, nonce: int,
                              previous_nonce: int) -> bool:
    """Fire when a wallet that had never transacted on HyperEVM starts to.

    HyperEVM is reachable from HyperCore without ever touching L1, and its public
    RPC caps log queries at 1000 blocks against ~1s blocks — so once funds move
    there, following them after the fact is not viable. The nonce going above
    zero is the moment worth catching, and it costs one request to watch.

    The target sent $23,000,000 to the USDC system address between 2026-06-12
    and 2026-08-28 while his own HyperEVM nonce stayed 0. If that changes, he has
    begun acting on a chain this project cannot reconstruct in arrears.
    """
    subject = "[EZEKIEL] CRITICAL: HyperEVM Activity Began"
    body = (
        f"{label} has sent its first transaction(s) on HyperEVM.\n\n"
        f"{address_line(wallet, 'Wallet')}\n"
        f"Transactions sent: {previous_nonce} -> {nonce}\n\n"
        f"Why this matters: HyperEVM is reachable from Hyperliquid without any\n"
        f"L1 footprint, and the public RPC limits log queries to 1000 blocks\n"
        f"against roughly one-second blocks — history cannot be reconstructed\n"
        f"after the fact. Funds moved there are followed from now, or not at all.\n\n"
        f"Action: check this address on a HyperEVM explorer and identify where it\n"
        f"sent funds.\n"
    )
    return _send_with_cooldown(f"hyperevm_active_{wallet.lower()}", 72, subject, body)


def alert_deposit_correlation(candidate: str, confidence: float, deposit_usd: float,
                              exit_usd: float, gap_hours: float, exit_source: str,
                              via: str | None = None) -> bool:
    """Fire when a target exit re-appears as a fresh HL deposit (re-linked
    across a CEX/cross-chain gap by amount + timing). `via` names the route
    the deposit took: the Arbitrum bridge, or Circle's CCTP from any chain."""
    subject = "[EZEKIEL] CRITICAL: Deposit/Withdrawal Correlation — Possible Re-entry Wallet"
    route = {"bridge": "through the Arbitrum bridge", "cctp": "through Circle (CCTP)"}.get(via)
    body = (
        f"A wallet deposited to Hyperliquid an amount closely matching a target exit,\n"
        f"shortly after — consistent with cashing out and re-entering on a fresh wallet.\n\n"
        f"{address_line(candidate, 'Candidate Wallet')}\n"
        + (f"Deposit route: {route}\n" if route else "")
        +         f"Correlation Confidence: {confidence:.0%}\n"
        f"Target exit: ${exit_usd:,.2f} ({exit_source})\n"
        f"This deposit: ${deposit_usd:,.2f}\n"
        f"Gap: {gap_hours:.1f} hours\n\n"
        f"This bridges the CEX gap a sophisticated migrator uses. It is being scanned\n"
        f"behaviorally — check the Recovery page.\n"
    )
    return _send_with_cooldown(f"correlation_{candidate.lower()}", 48, subject, body)


def alert_xyz_signature_match(candidate: str, shared_markets: list, score: float,
                              rarity_description: str = "") -> bool:
    """Fire when a wallet shares HIP-3 markets that MEASUREMENT classifies as rare.

    Callers must pass only markets from calibration.rare_markets(). The subject
    previously asserted "Same Rare HIP-3 Markets" for anything named `xyz:`, which
    made xyz:BRENTOIL — traded by ~26% of scanned wallets — look conclusive.
    """
    subject = "[EZEKIEL] HIGH: Shared Rare HIP-3 Markets (measured)"
    body = (
        f"A wallet shares HIP-3 markets with the target that the rolling rarity\n"
        f"calibration classifies as rare. Rarity is measured against the wallets\n"
        f"this scanner fingerprints, not assumed from the market name.\n\n"
        f"{address_line(candidate, 'Candidate Wallet')}\n"
        f"Rare shared markets: {', '.join(shared_markets[:8])}\n"
        f"Measured rarity: {rarity_description or 'see scan evidence'}\n"
        f"Behavioral similarity: {score:.0%}\n\n"
        f"This wallet also cleared the standard disposition checks (threshold,\n"
        f"percentile gate, persistence, style vetoes) and carries independent\n"
        f"corroboration — a shared market alone never triggers this alert.\n"
    )
    return _send_with_cooldown(f"xyz_sig_{candidate.lower()}", 48, subject, body)


def alert_linkage_match(candidate: str, reasons: list, score: float) -> bool:
    """Fire when L1 clustering (shared funder / address reuse) links a candidate."""
    subject = "[EZEKIEL] CRITICAL: On-Chain Linkage — Shared Funder / Address Reuse"
    reason_lines = "\n".join(f"  - {r}" for r in reasons)
    body = (
        f"On-chain clustering links a behavioral candidate to the target.\n"
        f"Address reuse is the highest-confidence heuristic in chain analysis.\n\n"
        f"{address_line(candidate, 'Candidate Wallet')}\n"
        f"Behavioral similarity: {score:.0%}\n"
        f"Linkage evidence:\n{reason_lines}\n"
    )
    return _send_with_cooldown(f"linkage_{candidate.lower()}", 72, subject, body)


def alert_risk_level(score: float, level: str, factors: list, wallet: str | None) -> bool:
    """Fire when the unified migration risk level rises into ELEVATED/CRITICAL."""
    subject = f"[EZEKIEL] {level}: Migration Risk {score:.0f}/100"
    factor_lines = "\n".join(f"  +{f['points']} {f['label']}" for f in factors[:8])
    body = (
        f"Unified migration risk has risen to {level} ({score:.0f}/100).\n\n"
        f"Contributing signals:\n{factor_lines}\n"
    )
    if wallet:
        body += f"\n{address_line(wallet, 'Strongest lead')}\n"
    body += "\nCheck the Recovery page.\n"
    return _send_with_cooldown(f"risk_{level.lower()}", 12, subject, body)


def alert_transfer_graph_discovery(node: dict, trigger_reasons: list,
                                   edges: list) -> bool:
    """Fire on a meaningful transfer-graph discovery.

    Carries the complete audit trail — full path, every transfer with amount and
    timestamp and reference, the classification and the specific evidence — so the
    conclusion can be checked rather than trusted.
    """
    wallet = node["wallet"]
    cls = node["classification"]
    conf = node["confidence"]

    severity = discovery_severity(cls)
    subject = f"[EZEKIEL] {severity}: {cls.replace('_', ' ').title()} ({conf:.0%} confidence)"

    path = address_path(node.get("path") or [wallet])
    reasons = "\n".join(f"  - {r}" for r in node.get("confidence_reasons", [])) or "  (none)"
    triggers = "\n".join(f"  - {r}" for r in trigger_reasons) or "  (none)"

    edge_lines = [
        f"  {e.get('timestamp') or 'unknown time'}  "
        f"{e.get('chain')}/{e.get('asset')}  "
        f"${float(e.get('amount_usd', 0)):,.2f}\n"
        f"      {e.get('src')} -> {e.get('dst')}\n"
        f"      ref: {e.get('ref') or 'n/a'}  via: {e.get('discovery_source')}"
        for e in edges[:15]
    ]
    edges_txt = "\n".join(edge_lines) or "  (no transfer detail)"
    more = f"\n  ... and {len(edges) - 15} further transfer(s)" if len(edges) > 15 else ""

    totals = node.get("totals", {})
    body = (
        f"{address_line(wallet, 'Wallet')}\n"
        f"Classification: {cls}\n"
        f"Linkage confidence: {conf:.0%}\n"
        f"Hops from target: {node.get('depth')}\n"
        f"First seen: {node.get('first_seen')}\n"
        f"Last seen:  {node.get('last_seen')}\n\n"
        f"WHY THIS ALERTED NOW\n{triggers}\n\n"
        f"RELATIONSHIP PATH\n{path}\n\n"
        f"EVIDENCE\n{reasons}\n\n"
        f"FLOWS\n"
        f"  Received from target: ${float(totals.get('received_from_target_usd', 0)):,.2f}\n"
        f"  Sent to target:       ${float(totals.get('sent_to_target_usd', 0)):,.2f}\n"
        f"  Transfers observed:   {totals.get('edge_count', 0)}\n\n"
        f"TRANSFERS\n{edges_txt}{more}\n\n"
    )
    if cls == "CORRELATION_LEAD":
        body += ("NOTE: no transfer between this wallet and the target was observed. The\n"
                 "link is an amount-and-timing match across a gap, which is a lead to\n"
                 "review and much weaker than an observed transfer.\n\n")
    elif cls in ("DIRECT_RECIPIENT", "OPERATIONAL_COUNTERPARTY"):
        body += ("NOTE: a transfer relationship is NOT proof of common ownership. This\n"
                 "wallet is recorded as a lead for review, not identified as the target.\n\n")
    body += "Full graph and paths: Recovery page -> Transfer Graph.\n"

    # Keyed on wallet + classification + confidence band, so a strengthened
    # finding re-alerts while a re-scan of unchanged state does not.
    band = int(conf * 10)
    return _send_with_cooldown(f"tg_{wallet.lower()}_{cls}_{band}", 48, subject, body)


def alert_collection_stale(age_minutes: float | None, threshold_minutes: float) -> bool:
    """Fire when data collection itself has stopped. This is the failure the rest
    of the alerting cannot detect — every other alert requires the collector to
    be running."""
    age_desc = f"{age_minutes:.0f} minutes" if age_minutes is not None else "unknown"
    subject = "[EZEKIEL] CRITICAL: Data Collection Has Stalled"
    body = (
        f"No new data has been collected for {age_desc} "
        f"(alert threshold: {threshold_minutes:.0f} minutes).\n\n"
        f"While collection is down the system cannot detect a migration, and "
        f"Hyperliquid serves only ~2000 recent entries per endpoint, so a long "
        f"enough gap would put older activity beyond reach.\n\n"
        f"Check: GitHub Actions tab -> 'Collect Trading Data' workflow.\n"
        f"Note: this repo's schedule is heavily throttled by GitHub (~14.9 runs/day "
        f"against a 15-minute request), so gaps of a few hours are normal; this "
        f"alert only fires past {threshold_minutes:.0f} minutes.\n"
        f"Common causes: exhausted Actions minutes, revoked workflow write "
        f"permissions, or a job failing before its commit step.\n"
    )
    return _send_with_cooldown("collection_stale", 24, subject, body)


def alert_account_value_drop(current: float, previous: float, drop_pct: float,
                             components: dict | None = None) -> bool:
    """Fire when the WHOLE account falls sharply.

    The breakdown is in the body because the previous version of this alert
    read the perp margin summary alone and announced a "52% drop — possible
    liquidation" on 2026-09-11 when he had moved money from perp to spot and
    bridged $7M to HyperEVM, with total equity flat. Naming where the value
    sits is what separates a liquidation from a transfer at a glance.
    """
    subject = f"[EZEKIEL] WARNING: Account Value Drop {drop_pct:.0%} — Possible Liquidation"
    breakdown = ""
    if components:
        def _fmt(v):
            return f"${v:,.2f}" if isinstance(v, (int, float)) else "unreadable"
        breakdown = (
            f"\nWhere the value sits now:\n"
            f"  Perp:      {_fmt(components.get('perp'))}\n"
            f"  HIP-3:     {_fmt(components.get('hip3'))}\n"
            f"  Spot USDC: {_fmt(components.get('spot_usdc'))}\n"
            f"  (plus {components.get('spot_other_tokens', 0)} non-USDC spot token(s), "
            f"not valued)\n")
    body = (
        f"The target wallet's total account value has dropped significantly.\n\n"
        f"Previous: ${previous:,.2f}\n"
        f"Current:  ${current:,.2f}\n"
        f"Change:   -{drop_pct:.1%}\n"
        f"{breakdown}\n"
        f"This may indicate a large loss, liquidation, or withdrawal. Money moved\n"
        f"between perp and spot is NOT a drop and no longer fires this alert.\n"
        f"A trader who has been wiped may migrate to a fresh wallet — monitor Recovery page.\n"
    )
    return send_alert(subject, body)
