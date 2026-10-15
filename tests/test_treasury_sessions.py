"""Treasury-session co-occurrence: sessions, windows, counting, chance and the hit bar."""

from src import treasury_sessions as ts

T = "0x" + "1" * 40
K = "0x" + "2" * 40      # a known wallet: measured like a stranger (positive control)
A = "0x" + "a" * 40
B = "0x" + "b" * 40
MIN = 60_000


def _act(user, kind="usdSend", t=0, status="ok", **payload):
    return {"user": user, "type": kind, "action": {"type": kind, **payload}, "status": status,
            "time": t, "block": 1, "index": 0, "nonce": t, "vault_address": None}


def test_target_events_take_his_actions_and_ledger_rows_since_the_start():
    actions = [{"type": "usdSend", "time": 100, "destination": A.upper()},
               {"type": "order", "time": 150}, {"type": "approveAgent", "time": 50}]
    ledger = [{"time": 120, "delta": {"type": "deposit", "usdc": "5"}},
              {"time": 130, "delta": {"type": "spotGenesis"}}, {"time": 10, "delta": {"type": "deposit"}}]
    events = ts.target_events(actions, ledger, start_ms=60)
    assert [(e["t"], e["type"]) for e in events] == [(100, "usdSend"), (120, "ledger:deposit")]
    assert events[0]["destination"] == A


def test_sessions_cluster_by_gap_and_collect_destinations():
    events = [{"t": 0, "type": "usdSend", "destination": A}, {"t": 5 * MIN, "type": "withdraw3",
                                                              "destination": B},
              {"t": 5 * MIN + ts.SESSION_GAP_MS + 1, "type": "approveAgent", "destination": None}]
    out = ts.sessions(events)
    assert len(out) == 2 and out[0]["n"] == 2 and out[0]["destinations"] == {A, B}
    assert out[0]["key"] == "0" and out[1]["start"] == events[2]["t"]
    assert ts.window(out[0]) == (-ts.PAD_MS, 5 * MIN + ts.PAD_MS)


def test_control_windows_are_the_same_span_on_other_days_clipped_to_the_dataset():
    s = {"start": 10 * 86_400_000, "end": 10 * 86_400_000 + MIN}
    ctl = ts.control_windows(s, dataset_start_ms=1 * 86_400_000, head_ms=20 * 86_400_000)
    assert ctl == [(3 * 86_400_000 - ts.PAD_MS, 3 * 86_400_000 + MIN + ts.PAD_MS),
                   (17 * 86_400_000 - ts.PAD_MS, 17 * 86_400_000 + MIN + ts.PAD_MS)]
    assert ts.control_windows(s, dataset_start_ms=1 * 86_400_000, head_ms=12 * 86_400_000) == [ctl[0]]
    assert ts.control_windows(s, dataset_start_ms=5 * 86_400_000, head_ms=12 * 86_400_000) == []


def test_present_users_count_once_and_skip_him_his_payees_bots_and_actions_touching_him():
    rows = [_act(A, t=1), _act(A, t=2, kind="withdraw3"),         # A: two actions, counts once
            _act(T, t=3),                                          # him
            _act(B, t=4, destination=T),                           # pays a cluster wallet: not co-occurrence
            _act(K, t=5, kind="sendToEvmWithData", destinationRecipient="0x" + "9" * 40),
            _act("0x" + "c" * 40, t=6, status="err"),              # failed
            _act("0x" + "d" * 40, t=7, kind="order")]              # trading
    rows += [_act("0x" + "f" * 40, t=100 + i) for i in range(ts.BOT_ACTIONS + 1)]   # a bot
    present = ts.present_users(rows, {T}, exclude={"0x" + "9" * 40})
    assert set(present) == {A, K}
    assert present[A] == {"n": 2, "kinds": {"usdSend": 1, "withdraw3": 1}}
    # The wallet he paid this session is excluded for the session.
    assert K not in ts.present_users(rows, {T}, exclude={K})


def test_fold_session_counts_sessions_and_controls_and_is_idempotent_by_key():
    state = ts.empty_state()
    s = {"key": "s1", "start": 0, "end": MIN, "n": 2, "destinations": set()}
    c = ts.fold_session(state, s, [_act(A, t=1), _act(K, t=2)], [[_act(A, t=9)], [_act(B, t=9)]], {T}, 5)
    assert c == {"present": 2, "controls": 2, "control_present": 2}
    assert state["sessions"] == 1 and state["controls"] == 2
    assert state["wallets"][A] == {"s": 1, "c": 1, "k": {"usdSend": 1}, "ss": ["s1"], "f": 5, "l": 5}
    assert state["wallets"][K]["s"] == 1 and state["wallets"][K]["c"] == 0
    assert state["wallets"][B]["s"] == 0 and state["wallets"][B]["c"] == 1
    assert ts.fold_session(state, s, [], [], {T}, 6) == {"skipped": True} and state["sessions"] == 1


def test_lift_p_and_the_hit_bar():
    n, c = 40, 80
    # Present in 8 of 40 sessions and 0 of 80 controls: rare by chance.
    strong = {"s": 8, "c": 0}
    assert ts.lift(strong, n, c) > ts.MIN_LIFT and ts.p_value(strong, n, c) < ts.MAX_P
    assert ts.is_hit(strong, n, c)
    # Present everywhere: a service, lift ~1.
    service = {"s": 40, "c": 80}
    assert abs(ts.lift(service, n, c) - 1.0) < 0.05 and not ts.is_hit(service, n, c)
    # Few sessions never qualify, however rare.
    assert not ts.is_hit({"s": 3, "c": 0}, n, c)
    assert ts.lift({"s": 1, "c": 0}, 0, 0) is None and ts.p_value({"s": 1, "c": 0}, 0, 0) is None
    assert ts.binomial_tail(0, 10, 0.1) == 1.0 and ts.binomial_tail(11, 10, 0.1) == 0.0
    assert abs(ts.binomial_tail(1, 1, 0.3) - 0.3) < 1e-12


def test_screen_ranks_by_sessions_then_p_and_excludes_and_cap_keeps_the_most_sessions():
    state = ts.empty_state()
    state.update(sessions=10, controls=20, wallets={
        A: {"s": 5, "c": 0, "k": {}, "ss": [], "f": 1, "l": 1},
        B: {"s": 5, "c": 10, "k": {}, "ss": [], "f": 1, "l": 2},
        K: {"s": 1, "c": 0, "k": {}, "ss": [], "f": 1, "l": 3},
        "0x" + "0" * 40: {"s": 0, "c": 3, "k": {}, "ss": [], "f": 1, "l": 4},
    })
    rows = ts.screen(state)
    assert [r["wallet"] for r in rows] == [A, B, K] and rows[0]["hit"] and not rows[1]["hit"]
    assert [r["wallet"] for r in ts.screen(state, exclude={A})] == [B, K]
    capped = ts.cap(state, limit=2)
    assert set(capped["wallets"]) == {A, B}
    assert ts.summary(state, [rows[0]])["wallets_with_2plus"] == 2
