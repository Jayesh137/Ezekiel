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
from src.utils import DATA_DIR, now_ms, read_cursor, save_latest, write_cursor

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

    The dashboard reads this file, so the outage becomes visible where the
    operator already looks. Never raises: a diagnostic that can take down the
    thing it diagnoses is worse than no diagnostic.
    """
    try:
        path = DATA_DIR / "alerts" / "latest.json"
        prev = {}
        if path.exists():
            try:
                with open(path) as f:
                    prev = json.load(f) or {}
            except (OSError, ValueError):
                prev = {}
        now = datetime.now(UTC).isoformat()
        fails = 0 if delivered else int(prev.get("consecutive_failures", 0) or 0) + 1
        recent = list(prev.get("recent") or [])
        recent.append({"at": now, "subject": subject,
                       "delivered": delivered, "reason": reason})
        state = {
            "updated_at": now,
            "healthy": delivered,
            "consecutive_failures": fails,
            # How many alerts the operator was never told about.
            "undelivered": 0 if delivered else int(prev.get("undelivered", 0) or 0) + 1,
            "last_success_at": now if delivered else prev.get("last_success_at"),
            "last_failure_at": prev.get("last_failure_at") if delivered else now,
            "last_failure_reason": prev.get("last_failure_reason") if delivered else reason,
            "recent": recent[-20:],
        }
        save_latest(str(DATA_DIR / "alerts"), state)
    except Exception as e:  # noqa: BLE001 - must never break alerting
        print(f"[alerts] could not record delivery health: {type(e).__name__}: {e}")


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
                              tx_hash: str | None = None) -> bool:
    """Fire when a cluster wallet hands value or control to an address outside
    the cluster, as seen in its own Hyperliquid actions.

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
    else:
        subject = f"[EZEKIEL] CRITICAL: {kind} to an address outside the cluster"
        lead = (f"A cluster wallet performed `{kind}` towards an address the roster does\n"
                f"not know as his.\n\n")
    body = (
        lead
        + f"{address_line(wallet, 'Wallet')}\n"
        f"{address_line(destination, 'Destination')}\n"
        f"Amount: {amount if amount is not None else 'n/a'} {token or ''}\n"
        f"When: {when or 'unknown'}\n"
        f"Hyperliquid tx: {tx_hash or 'n/a'}\n\n"
        f"This is the account's OWN action, read from the Hyperliquid explorer,\n"
        f"not an inference from flow or style. Check the destination on\n"
        f"Hyperliquid at once: an account funded this way is a new wallet he\n"
        f"controls until shown otherwise.\n"
    )
    key = f"foreign_{kind}_{(destination or '').lower()}"
    return _send_with_cooldown(key, 72, subject, body)


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
                              exit_usd: float, gap_hours: float, exit_source: str) -> bool:
    """Fire when a target exit re-appears as a fresh HL bridge deposit (re-linked
    across a CEX/cross-chain gap by amount + timing)."""
    subject = "[EZEKIEL] CRITICAL: Deposit/Withdrawal Correlation — Possible Re-entry Wallet"
    body = (
        f"A wallet deposited to Hyperliquid an amount closely matching a target exit,\n"
        f"shortly after — consistent with cashing out and re-entering on a fresh wallet.\n\n"
        f"{address_line(candidate, 'Candidate Wallet')}\n"
        f"Correlation Confidence: {confidence:.0%}\n"
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

    severity = "CRITICAL" if cls == "MIGRATION_CANDIDATE" else (
        "HIGH" if cls == "POSSIBLE_LINKED_WALLET" else "INFO")
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


def alert_account_value_drop(current: float, previous: float, drop_pct: float) -> bool:
    subject = f"[EZEKIEL] WARNING: Account Value Drop {drop_pct:.0%} — Possible Liquidation"
    body = (
        f"The target wallet's account value has dropped significantly.\n\n"
        f"Previous: ${previous:,.2f}\n"
        f"Current:  ${current:,.2f}\n"
        f"Change:   -{drop_pct:.1%}\n\n"
        f"This may indicate a large loss, liquidation, or withdrawal.\n"
        f"A trader who has been wiped may migrate to a fresh wallet — monitor Recovery page.\n"
    )
    return send_alert(subject, body)
