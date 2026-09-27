from datetime import UTC, datetime

from src.discovery_store import DiscoveryStore
from src.feed_health import _age_minutes, assess
from src.market_discovery import collect_once


def test_failed_poll_does_not_refresh_last_successful_read(tmp_path):
    config = {'discovery': {'discover_markets': False, 'markets': ['BTC'], 'max_markets': 1}}
    with DiscoveryStore(tmp_path / 'discovery.db') as store:
        collect_once(config, store, fetch=lambda body: [], now_ms=1000)
        collect_once(config, store, fetch=lambda body: {'ok': False, 'error': 'timeout'}, now_ms=2000)
        summary = store.export_summary()
        assert summary['last_successful_read_ms'] == 1000
        assert summary['last_positive_observation_ms'] is None
        assert summary['computed_at_ms'] >= 2000


def test_feed_health_understands_milliseconds_and_rejects_invalid_time():
    now = datetime(2026, 9, 26, tzinfo=UTC)
    assert _age_minutes(now.timestamp() * 1000 - 60000, now) == 1
    assert _age_minutes(float('inf'), now) is None


def test_new_discovery_reports_are_monitored_when_missing(tmp_path):
    from src.feed_health import OTHER_FEEDS
    missing = {p['feed'] for p in assess(OTHER_FEEDS, tmp_path)}
    assert {'public wallet discovery', 'funding route index', 'successor investigations'} <= missing
