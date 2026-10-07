"""Stranger and same-operator panels for the tooling tests."""

from src.study import panels, records, tooling

T = "0x45d26f28196d226497130c4bac709d808fed4029"
M, S1, S2, X = ("0x" + c * 40 for c in "1234")
HIS_STYLE = {"style": "PROGRAM_IOC5", "flags": {"client_ids": False, "triggers": False,
                                                "maker": False}}
MAKER = {"style": "MAKER", "flags": {"client_ids": True, "triggers": False, "maker": True}}


def snap(style, cadence=None, clips=None, shares=None):
    return {"orders_seen": 500, "style": style, "cadence": cadence, "clip_table": clips,
            "clip_notionals": None, "program_runs": 0, "ioc5": None,
            "shares": shares or {"client_ids": 0.0, "maker": 0.0, "triggers": 0.0}}


def test_families_join_both_sources_and_drop_singletons():
    surface = {"subaccounts": {S1: {"master": M}}}
    habits = {M: {"subaccounts": [S2]}, X: {"subaccounts": []}}
    assert panels.families(surface, habits) == {M: sorted([M, S1, S2])}


def test_panels_never_contain_the_target():
    surface = {"subaccounts": {S1: {"master": T}, T: {"master": M}}}
    habits = {T: {"orders_seen": 900, "style": HIS_STYLE}, X: {"orders_seen": 900, "style": MAKER}}
    assert panels.families(surface, habits, exclude={T}) == {}
    assert [r["wallet"] for r in panels.strangers(habits, exclude={T})] == [X]


def test_family_pairs_count_style_agreement_and_trait_disagreement():
    fams = {M: [M, S1, S2]}
    snaps = {M: snap(MAKER), S1: snap(MAKER), S2: snap(HIS_STYLE)}
    # The star is (M, S1) and (M, S2): S1 agrees with M, S2 does not.
    stats = panels.family_t1(panels.member_pairs(fams, snaps))
    assert stats["agree"] == (1, 2)
    assert stats["mismatch"]["client_ids"] == (1, 2)


def test_a_large_family_contributes_one_pair_per_extra_member():
    # k measured members give k-1 pairs, not k(k-1)/2: one big operator cannot supply
    # the whole same-operator panel (spec 8.2's 40-pair bar).
    members = [f"0x{i:040x}" for i in range(10)]
    snaps = {m: snap(MAKER) for m in members}
    pairs = panels.member_pairs({members[0]: members}, snaps)
    assert len(pairs) == 9
    # Equal snapshots compare equal, so identity is what shows who was paired with whom.
    assert all(a is snaps[members[0]] and b is snaps[m]
               for (a, b), m in zip(pairs, members[1:], strict=True))


def test_the_star_centres_on_the_first_measured_member():
    members = [f"0x{i:040x}" for i in range(4)]
    snaps = {members[1]: snap(MAKER), members[2]: snap(HIS_STYLE), members[3]: snap(MAKER)}
    pairs = panels.member_pairs({"a": members}, snaps)
    # members[0] has no snapshot, so members[1] is the centre.
    assert pairs == [(snaps[members[1]], snaps[members[2]]), (snaps[members[1]], snaps[members[3]])]


def test_every_family_is_its_own_star_and_one_measured_member_forms_no_pair():
    a, b, c = ([f"0x{x}{i:039x}" for i in range(n)] for x, n in (("a", 3), ("b", 4), ("c", 2)))
    snaps = {m: snap(MAKER) for m in a + b + c[:1]}  # c's second member was never measured
    pairs = panels.member_pairs({"a": a, "b": b, "c": c}, snaps)
    assert len(pairs) == 2 + 3 + 0
    assert panels.member_pairs({"d": [S1, S2]}, {}) == []


def test_unmeasurable_members_are_left_out_not_counted_as_disagreeing():
    fams = {M: [M, S1]}
    stats = panels.family_t1(panels.member_pairs(fams, {M: snap(MAKER), S1: snap(None)}))
    assert stats["agree"] == (0, 0)


def test_stranger_rates_and_rhythm_values():
    hist = [0] * records.CADENCE_BINS
    hist[17] = 300
    rows = [{"wallet": X, "orders_seen": 500, "style": HIS_STYLE, "cadence": hist,
             "shares": {"client_ids": 0.9, "maker": 0.0, "triggers": 0.0}},
            {"wallet": M, "orders_seen": 500, "style": MAKER, "cadence": None,
             "shares": {"client_ids": 1.0, "maker": 1.0, "triggers": 0.0}}]
    assert panels.stranger_t1(rows, HIS_STYLE) == (1, 2)
    assert panels.stranger_trait_rates(rows) == {"client_ids": 1.0, "triggers": 0.0, "maker": 0.5}
    assert panels.stranger_t2(rows, hist) == [0.0, float("inf")]
    assert panels.stranger_t3(rows, tooling.snapshot_signature({})) == [float("-inf")] * 2


def test_a_stranger_whose_style_is_undecidable_is_unknown_for_t1_not_a_non_match():
    # An IOC-dominant stranger with too few measured offsets has style None
    # (tooling.style). It is unknown, so it leaves both k and n: counting it in n
    # would lower the stranger match rate behind the 2% bar and make a false "for"
    # easier.
    rows = [{"wallet": X, "orders_seen": 500, "style": HIS_STYLE, "cadence": None,
             "shares": {"client_ids": 0.0, "maker": 0.0, "triggers": 0.0}},
            {"wallet": M, "orders_seen": 500, "style": MAKER, "cadence": None,
             "shares": {"client_ids": 1.0, "maker": 1.0, "triggers": 0.0}},
            {"wallet": S1, "orders_seen": 500, "style": None, "cadence": None,
             "shares": {"client_ids": 0.0, "maker": 0.0, "triggers": 0.0}}]
    assert panels.stranger_t1(rows, HIS_STYLE) == (1, 2)
    assert panels.stranger_t1(rows[2:], HIS_STYLE) == (0, 0)


def test_a_measurable_stranger_has_at_least_a_hundred_orders_and_comes_back_lowercased():
    habits = {X.upper(): {"orders_seen": tooling.MIN_ORDERS},
              M: {"orders_seen": tooling.MIN_ORDERS - 1},
              S1: {"orders_seen": None},
              S2: "not a row"}
    assert [r["wallet"] for r in panels.strangers(habits)] == [X]


def test_a_family_whose_master_is_excluded_goes_whole_and_exclusion_ignores_case():
    surface = {"subaccounts": {S1: {"master": T}, S2: {"master": T}}}
    assert panels.families(surface, {}, exclude={T}) == {}
    assert panels.families(surface, {}, exclude={T.upper()}) == {}
    # Addresses from different sources meet in one family whatever their case.
    mixed = {"subaccounts": {S1.upper(): {"master": M.upper()}}}
    assert panels.families(mixed, {M: {"subaccounts": [S2]}}) == {M: sorted([M, S1, S2])}


def test_members_without_a_snapshot_form_no_pairs():
    pairs = panels.member_pairs({M: [M, S1, S2]}, {M: snap(MAKER), S2: snap(MAKER)})
    assert pairs == [(snap(MAKER), snap(MAKER))]


def test_rhythm_needs_two_hundred_gaps_on_every_side_that_is_compared():
    def hist(n):
        h = [0] * records.CADENCE_BINS
        h[17] = n
        return h
    enough, short = hist(tooling.MIN_GAPS), hist(tooling.MIN_GAPS - 1)
    pairs = [(snap(None, cadence=enough), snap(None, cadence=enough)),
             (snap(None, cadence=enough), snap(None, cadence=short)),
             (snap(None, cadence=short), snap(None, cadence=enough)),
             (snap(None, cadence=None), snap(None, cadence=enough))]
    assert panels.family_t2(pairs) == [0.0]
    rows = [{"cadence": enough}, {"cadence": short}, {"cadence": None}]
    assert panels.stranger_t2(rows, enough) == [0.0, float("inf"), float("inf")]


def test_family_clips_leave_out_pairs_with_nothing_to_compare():
    both = {"ZEC": 1.0, "BTC": 0.1}
    pairs = [(snap(MAKER, clips=both), snap(MAKER, clips=both)),
             (snap(MAKER, clips={"ZEC": 1.0}), snap(MAKER, clips={"SOL": 3.0}))]
    assert panels.family_t3(pairs) == [1.0]


def test_a_trait_is_dominant_only_above_half_and_an_empty_panel_has_no_rates():
    def row(share):
        return {"wallet": X, "orders_seen": 500, "style": MAKER, "cadence": None,
                "shares": {"client_ids": share, "maker": 0.0, "triggers": 0.0}}
    assert panels.stranger_trait_rates([row(0.5), row(0.5001)])["client_ids"] == 0.5
    assert panels.stranger_trait_rates([]) == {}
