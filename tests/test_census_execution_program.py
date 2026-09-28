"""Census population selection and the resumable measurement loop."""

from scripts import census_execution_program as census


def _row(addr, value, week_vlm):
    return {"ethAddress": addr, "accountValue": str(value),
            "windowPerformances": [["week", {"vlm": str(week_vlm)}]]}


def test_population_drops_small_idle_and_extreme_volume_accounts():
    lb = [_row("0xbig", 1_000_000, 5_000_000), _row("0xsmall", 100, 5_000_000),
          _row("0xidle", 1_000_000, 0), _row("0xMM", 1_000_000, 9_000_000_000)]
    assert census.population(lb, exclude=set()) == ["0xbig"]


def test_population_excludes_the_configured_cluster():
    lb = [_row("0xTarget", 1_000_000, 5_000_000), _row("0xother", 1_000_000, 4_000_000)]
    assert census.population(lb, exclude={"0xtarget"}) == ["0xother"]


def test_population_orders_by_week_volume_descending():
    lb = [_row("0xa", 1_000_000, 1_000_000), _row("0xb", 1_000_000, 9_000_000)]
    assert census.population(lb, exclude=set()) == ["0xb", "0xa"]
