# src/alerts.py
"""Email alert system via Brevo SMTP."""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_alert(subject: str, body: str, html_body: str | None = None) -> bool:
    """Send an email alert. Returns True if sent, False if skipped/failed."""
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
    return send_alert(subject, body)


def alert_new_wallet_found(source_wallet: str, new_wallet: str, method: str, confidence: float) -> bool:
    subject = f"[EZEKIEL] {'CRITICAL' if method == 'fund_trace' else 'HIGH'}: New Linked Wallet Detected"
    body = (
        f"New Wallet: {new_wallet}\n"
        f"Detection Method: {method}\n"
        f"Confidence: {confidence:.0%}\n"
        f"Source Wallet: {source_wallet}\n"
    )
    return send_alert(subject, body)


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
    return send_alert(subject, body)


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
    return send_alert(subject, body)
