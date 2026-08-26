"""Generic provider cache and history tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import database


def test_complete_provider_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.db")
    database.init_database()
    database.TickerCache.set(
        ticker="BRK.B",
        source="replacement_provider",
        status="NOT_HALAL",
        company_name="Berkshire Hathaway Inc.",
        quote_type="EQUITY",
        state="SUCCESS",
        asset_type="STOCK",
        url="https://example.test/BRK.B",
        evidence="BRK.B is not compliant",
        methodology="AAOIFI",
    )
    cached = database.TickerCache.get("BRK.B", "replacement_provider")
    assert cached["company_name"] == "Berkshire Hathaway Inc."
    assert cached["evidence"] == "BRK.B is not compliant"
    assert cached["asset_type"] == "STOCK"


def test_history_stores_arbitrary_provider_results(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "test.db")
    database.init_database()
    database.CheckHistory.record(
        user_id=7,
        ticker="SPUS",
        final_status="HALAL",
        provider_results={
            "daleel": {"status": "HALAL", "state": "SUCCESS"},
            "future_provider": {"status": "ERROR", "state": "NETWORK_ERROR"},
        },
        is_provisional=True,
        confirmation_count=1,
    )
    history = database.CheckHistory.get_user_history(7)
    assert history[0]["is_provisional"] == 1
    assert history[0]["confirmation_count"] == 1
    assert "future_provider" in history[0]["provider_results"]
