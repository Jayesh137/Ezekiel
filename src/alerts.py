# src/alerts.py
"""Email alert system via Brevo SMTP."""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from src.utils import read_cursor, write_cursor, now_ms

# Once a send fails (e.g. bad SMTP credentials), it will keep failing for the
# rest of this run. Short-circuit so a batch of alerts doesn't attempt hundreds
# of dead SMTP connections and blow the job timeout / spam the log.
_smtp_disabled_this_run = False


def _cooldown_ok(key: str, hours: float) -> bool:
    """Rate-limit repeat alerts. Cursor is only written after a successful send."""
    last = read_cursor(f"alert_{key}")
    return not last or (now_ms() - last) >= hours * 3600 * 1000


def _send_with_cooldown(key: str, hours: float, subject: str, body: str) -> bool:
    if not _cooldown_ok(key, hours):
        print(f"[alerts] Cooldown active for {key}, skipping: {subject}")
        return False
    if send_alert(subject, body):
        write_cursor(f"alert_{key}", now_ms())
        return True
    return False


def send_alert(subject: str, body: str, html_body: str | None = None) -> bool:
    """Send an email alert. Returns True if sent, False if skipped/failed."""
    global _smtp_disabled_this_run

    if _smtp_disabled_this_run:
        print(f"[alerts] SMTP disabled after earlier failure this run, skipping: {subject}")
        return False

    smtp_key = os.environ.get("BREVO_SMTP_KEY")
    alert_email = os.environ.get("ALERT_EMAIL")

    if not smtp_key or not alert_email:
        print(f"[alerts] SMTP not configured. Alert: {subject}")
        print(f"[alerts] {body[:200]}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Ezekiel Alerts <{alert_email}>"
    msg["To"] = alert_email

    msg.attach(MIMEText(body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP("smtp-relay.brevo.com", 587) as server:
            server.starttls()
            server.login("apikey", smtp_key)
            server.sendmail(msg["From"], [alert_email], msg.as_string())
        print(f"[alerts] Sent: {subject}")
        return True
    except Exception as e:
        print(f"[alerts] Failed to send: {e}")
        _smtp_disabled_this_run = True
        print("[alerts] Disabling further sends for this run.")
        return False


def alert_fund_movement(wallet: str, amount: str, destination: str, tx_hash: str) -> bool:
    subject = "[EZEKIEL] CRITICAL: Fund Movement Detected"
    body = (
        f"Wallet: {wallet}\n"
        f"Event: Withdrawal of {amount} USDC\n"
        f"Destination: {destination}\n"
        f"TX Hash: {tx_hash}\n"
        f"\nTracing destination wallet..."
    )
    return _send_with_cooldown(f"fund_movement_{tx_hash.lower()}", 24, subject, body)


def alert_new_wallet_found(source_wallet: str, new_wallet: str, method: str, confidence: float) -> bool:
    subject = f"[EZEKIEL] {'CRITICAL' if method == 'fund_trace' else 'HIGH'}: New Linked Wallet Detected"
    body = (
        f"New Wallet: {new_wallet}\n"
        f"Detection Method: {method}\n"
        f"Confidence: {confidence:.0%}\n"
        f"Source Wallet: {source_wallet}\n"
    )
    return _send_with_cooldown(f"new_wallet_{new_wallet.lower()}", 72, subject, body)


def alert_behavioral_match(candidate: str, score: float, dimensions: dict) -> bool:
    subject = f"[EZEKIEL] HIGH: Behavioral Match ({score:.0%} similarity)"
    dim_lines = "\n".join(
        f"  - {k}: {v:.2f}" for k, v in sorted(dimensions.items(), key=lambda x: -x[1])
    )
    body = (
        f"Candidate Wallet: {candidate}\n"
        f"Similarity Score: {score:.2f} / 1.00\n\n"
        f"Matching Dimensions:\n{dim_lines}\n"
    )
    return _send_with_cooldown(f"behavioral_{candidate.lower()}", 24, subject, body)


def alert_combined_match(candidate: str, score: float, flow_amount: str, flow_method: str) -> bool:
    """Fire when the same wallet appears in both fund-flow tracing AND behavioral matching."""
    subject = "[EZEKIEL] CRITICAL: Fund Trace + Behavioral Match on Same Wallet"
    body = (
        f"HIGHEST CONFIDENCE SIGNAL — BOTH VECTORS POINT TO SAME WALLET\n\n"
        f"Candidate Wallet: {candidate}\n"
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
        f"Candidate: {candidate}\n"
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
        f"Candidate: {candidate}\n"
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
        f"Destination: {destination}\n"
        f"Sent to this wallet: ${out_usd:,.2f}\n"
        f"Received from this wallet: ${in_usd:,.2f}\n"
        f"Two-way relationship: {'YES — very likely same owner' if bidirectional else 'no'}\n"
        f"Tokens: {token_str}\n\n"
        f"Action: this wallet is now a top migration candidate. Check the Recovery "
        f"page — it is being behaviorally scanned automatically.\n"
    )
    return _send_with_cooldown(f"hl_transfer_{destination.lower()}", 72, subject, body)


def alert_deposit_correlation(candidate: str, confidence: float, deposit_usd: float,
                              exit_usd: float, gap_hours: float, exit_source: str) -> bool:
    """Fire when a target exit re-appears as a fresh HL bridge deposit (re-linked
    across a CEX/cross-chain gap by amount + timing)."""
    subject = "[EZEKIEL] CRITICAL: Deposit/Withdrawal Correlation — Possible Re-entry Wallet"
    body = (
        f"A wallet deposited to Hyperliquid an amount closely matching a target exit,\n"
        f"shortly after — consistent with cashing out and re-entering on a fresh wallet.\n\n"
        f"Candidate Wallet: {candidate}\n"
        f"Correlation Confidence: {confidence:.0%}\n"
        f"Target exit: ${exit_usd:,.2f} ({exit_source})\n"
        f"This deposit: ${deposit_usd:,.2f}\n"
        f"Gap: {gap_hours:.1f} hours\n\n"
        f"This bridges the CEX gap a sophisticated migrator uses. It is being scanned\n"
        f"behaviorally — check the Recovery page.\n"
    )
    return _send_with_cooldown(f"correlation_{candidate.lower()}", 48, subject, body)


def alert_xyz_signature_match(candidate: str, shared_markets: list, score: float) -> bool:
    """Fire when a wallet trades the same rare xyz: HIP-3 markets as the target.
    Almost nobody trades these, so overlap is near-conclusive."""
    subject = "[EZEKIEL] CRITICAL: xyz: Signature Match — Same Rare HIP-3 Markets"
    body = (
        f"A wallet is trading the same rare HIP-3 (xyz:) markets as the target.\n"
        f"Almost no one trades these — this is one of the strongest behavioral tells.\n\n"
        f"Candidate Wallet: {candidate}\n"
        f"Shared xyz: markets: {', '.join(shared_markets[:8])}\n"
        f"Behavioral similarity: {score:.0%}\n"
    )
    return _send_with_cooldown(f"xyz_sig_{candidate.lower()}", 48, subject, body)


def alert_linkage_match(candidate: str, reasons: list, score: float) -> bool:
    """Fire when L1 clustering (shared funder / address reuse) links a candidate."""
    subject = "[EZEKIEL] CRITICAL: On-Chain Linkage — Shared Funder / Address Reuse"
    reason_lines = "\n".join(f"  - {r}" for r in reasons)
    body = (
        f"On-chain clustering links a behavioral candidate to the target.\n"
        f"Address reuse is the highest-confidence heuristic in chain analysis.\n\n"
        f"Candidate Wallet: {candidate}\n"
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
        body += f"\nStrongest lead: {wallet}\n"
    body += "\nCheck the Recovery page.\n"
    return _send_with_cooldown(f"risk_{level.lower()}", 12, subject, body)


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
