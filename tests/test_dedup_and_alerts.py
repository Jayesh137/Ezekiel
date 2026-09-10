# tests/test_dedup_and_alerts.py
"""Targeted tests for changed critical behavior: batch dedup and alert cooldowns."""
import json
import tempfile

from src import alerts, tracer
from src.utils import append_records, load_all_records


def test_append_records_dedupes_within_batch():
    with tempfile.TemporaryDirectory() as d:
        records = [
            {"tid": 1, "coin": "BTC"},
            {"tid": 1, "coin": "BTC"},  # within-batch dup
            {"tid": 2, "coin": "ETH"},
        ]
        assert append_records(d, records, key_field="tid") == 2
        assert len(load_all_records(d)) == 2


def test_append_records_keyless_records_not_deduped():
    with tempfile.TemporaryDirectory() as d:
        records = [{"coin": "BTC"}, {"coin": "ETH"}]  # no key field
        assert append_records(d, records, key_field="tid") == 2


def test_append_records_dedupes_against_existing():
    with tempfile.TemporaryDirectory() as d:
        append_records(d, [{"tid": 1}], key_field="tid")
        assert append_records(d, [{"tid": 1}, {"tid": 3}], key_field="tid") == 1


def test_alert_cooldown(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(alerts, "send_alert", lambda s, b, h=None: sent.append(s) or True)
    monkeypatch.setattr(alerts, "read_cursor", lambda name: cursors.get(name, 0))
    monkeypatch.setattr(alerts, "write_cursor", lambda name, v: cursors.__setitem__(name, v))
    cursors = {}

    assert alerts.alert_behavioral_match("0xABC", 0.95, {"timing_profile": 0.9}) is True
    assert alerts.alert_behavioral_match("0xABC", 0.95, {"timing_profile": 0.9}) is False
    assert alerts.alert_behavioral_match("0xDEF", 0.95, {"timing_profile": 0.9}) is True
    assert len(sent) == 2


def test_alert_fund_movement_labels_the_real_asset_and_chain(monkeypatch):
    """Before the tracer read the substrate, every fund-movement alert WAS
    Arbitrum USDC by construction, so hardcoding "USDC" in the body was
    correct. It now spans every chain and asset, so a USDT withdrawal must
    not be reported to the user as USDC — that would be a false statement in
    the one artifact this system exists to produce."""
    sent = []
    monkeypatch.setattr(alerts, "send_alert", lambda s, b, h=None: sent.append((s, b)) or True)
    monkeypatch.setattr(alerts, "read_cursor", lambda name: cursors.get(name, 0))
    monkeypatch.setattr(alerts, "write_cursor", lambda name, v: cursors.__setitem__(name, v))
    cursors = {}

    assert alerts.alert_fund_movement("0xw", "$5,000.00", "0xd", "0xhash",
                                      asset="USDT", chain="base") is True
    subject, body = sent[0]
    assert "USDT" in subject and "base" in subject
    assert "USDT" in body and "base" in body
    assert "USDC" not in subject and "USDC" not in body


def test_alert_fund_movement_defaults_preserve_pre_substrate_behavior(monkeypatch):
    """A caller that doesn't pass asset/chain (there is currently none, but the
    parameters are defaulted defensively) must keep reporting Arbitrum USDC,
    exactly as this function always has."""
    sent = []
    monkeypatch.setattr(alerts, "send_alert", lambda s, b, h=None: sent.append((s, b)) or True)
    monkeypatch.setattr(alerts, "read_cursor", lambda name: cursors.get(name, 0))
    monkeypatch.setattr(alerts, "write_cursor", lambda name, v: cursors.__setitem__(name, v))
    cursors = {}

    assert alerts.alert_fund_movement("0xw", "$5,000.00", "0xd", "0xhash2") is True
    subject, body = sent[0]
    assert "USDC" in subject and "arbitrum" in subject
    assert "USDC" in body and "arbitrum" in body


def test_alert_fund_movement_renders_an_unambiguous_dollar_qualified_message(monkeypatch):
    """Round 2 made the asset label dynamic but left the number unqualified:
    "Withdrawal of 2,000,000.00 ETH" reads as a token quantity. That is only
    harmless today because every asset reaching this path is priced at par; the
    day a MAJORS price_lookup exists it becomes a false statement. Assert the
    full rendered strings, not a substring, so a future reword cannot silently
    reintroduce that ambiguity."""
    sent = []
    monkeypatch.setattr(alerts, "send_alert", lambda s, b, h=None: sent.append((s, b)) or True)
    monkeypatch.setattr(alerts, "read_cursor", lambda name: cursors.get(name, 0))
    monkeypatch.setattr(alerts, "write_cursor", lambda name, v: cursors.__setitem__(name, v))
    cursors = {}

    assert alerts.alert_fund_movement("0xwallet", "$2,000,000.00", "0xdest", "0xhash3",
                                      asset="ETH", chain="base") is True
    subject, body = sent[0]
    assert subject == "[EZEKIEL] CRITICAL: Fund Movement Detected ($2,000,000.00 of ETH on base)"
    assert "Event: Withdrew $2,000,000.00 of ETH on base\n" in body
    assert "2,000,000.00 ETH" not in body       # the old, ambiguous "N ASSET" form
    assert "2,000,000.00 ETH" not in subject


def test_unique_destinations_dedupes_and_skips_dust():
    wallet = "0xWALLET"
    outbound = [
        {"to": "0xDEST", "value": "0"},          # zero-value dust -> dropped
        {"to": "0xDEST", "value": "0"},          # more dust to same dest
        {"to": "0xDEST", "value": "5000000"},    # real 5 USDC -> representative
        {"to": "0xDEST", "value": "1000000"},    # smaller, same dest -> collapsed
        {"to": wallet, "value": "9000000"},      # self-transfer -> dropped
        {"to": "0xOTHER", "value": "2000000"},   # distinct dest
    ]
    result = tracer.unique_destinations(outbound, wallet)
    dests = [t["to"] for t in result]
    assert dests == ["0xDEST", "0xOTHER"]                 # deduped, value-sorted
    assert result[0]["value"] == "5000000"                # kept largest per dest


def test_unique_destinations_respects_cap():
    outbound = [{"to": f"0x{i:040x}", "value": "1000000"} for i in range(200)]
    assert len(tracer.unique_destinations(outbound, "0xWALLET")) == tracer.MAX_DESTINATIONS


def test_send_alert_short_circuits_after_failure(monkeypatch):
    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)
    monkeypatch.setenv("BREVO_SMTP_LOGIN", "test-login@smtp-brevo.example")
    monkeypatch.setenv("BREVO_SMTP_KEY", "key")
    monkeypatch.setenv("ALERT_EMAIL", "me@example.com")

    attempts = []

    def boom(*a, **k):
        attempts.append(1)
        raise OSError("(535, b'5.7.8 Authentication failed')")

    monkeypatch.setattr(alerts.smtplib, "SMTP", boom)

    assert alerts.send_alert("s1", "b1") is False   # attempts a real connect, fails
    assert alerts.send_alert("s2", "b2") is False   # short-circuits, no connect
    assert len(attempts) == 1


# --- instant channels ---------------------------------------------------------

def test_webhook_delivery_counts_even_when_email_is_unconfigured(monkeypatch, tmp_path):
    """Email has never delivered on this deployment; Telegram must not be
    marked undelivered because of it."""
    import src.alerts as alerts_mod

    posted = []

    class R:
        status_code = 200

    def fake_post(url, **kw):
        posted.append((url, kw))
        return R()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("NTFY_TOPIC", "ezekiel-test")
    monkeypatch.delenv("BREVO_SMTP_LOGIN", raising=False)
    monkeypatch.delenv("BREVO_SMTP_KEY", raising=False)
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    monkeypatch.setattr(alerts_mod, "_smtp_disabled_this_run", False)
    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    assert alerts_mod.send_alert("[EZEKIEL] CRITICAL: x", "body") is True
    urls = [u for u, _ in posted]
    assert any("api.telegram.org/bottok/sendMessage" in u for u in urls)
    assert any(u.endswith("/ezekiel-test") for u in urls)
    ntfy_kw = next(kw for u, kw in posted if u.endswith("/ezekiel-test"))
    assert ntfy_kw["headers"]["Priority"] == "high"
    health = json.load(open(alerts_mod.DATA_DIR / "alerts" / "latest.json"))
    assert health["healthy"] is True
    assert "telegram" in health["recent"][-1]["reason"]
    assert "email" in health["recent"][-1]["reason"]     # the email failure is still named


def test_a_rejected_webhook_does_not_count(monkeypatch):
    import src.alerts as alerts_mod

    class R:
        status_code = 403

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("BREVO_SMTP_LOGIN", raising=False)
    monkeypatch.setattr(alerts_mod, "_smtp_disabled_this_run", False)
    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: R())
    assert alerts_mod.send_alert("[EZEKIEL] INFO: x", "body") is False
