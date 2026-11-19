from datetime import date

import pandas as pd
import pytest

from bourse.fmp.client import FMPClient, FMPError, PlanRestrictedError, QuotaExceededError
from bourse.fmp.fake import FakeFMPClient
from bourse.ingestion import run_ingestion
from bourse.pipeline import run_pipeline
from bourse.transform.silver import _merge, payload_to_rows, snake

from .conftest import TEST_TICKERS


def test_snake_case():
    assert snake("weightedAverageShsOutDil") == "weighted_average_shs_out_dil"
    assert snake("epsDiluted") == "eps_diluted"
    assert snake("Stock Price") == "stock_price"
    assert snake("year10") == "year10"


def test_payload_v3_historical_format():
    rows = payload_to_rows({"symbol": "KO", "historical": [{"date": "2024-01-02", "close": 1}]}, "KO")
    assert rows == [{"date": "2024-01-02", "close": 1, "symbol": "KO"}]


def test_merge_aligns_types():
    old = pd.DataFrame({"k": [1], "v": [1.5]})
    new = pd.DataFrame({"k": [1, 2], "v": ["a", "b"]})
    out = _merge(old, new, ["k"])
    assert len(out) == 2 and out["v"].tolist() == ["a", "b"]


def test_full_demo_pipeline(demo_store):
    val = demo_store.read("gold", "valuation")
    assert set(val["symbol"]) == set(TEST_TICKERS)
    crit = demo_store.read("gold", "graham_criteria")
    assert crit.groupby("symbol").size().eq(7).all()
    assert set(crit["status"]) <= {"pass", "fail", "partial", "na"}
    screener = demo_store.read("gold", "screener")
    assert {"name", "sector", "fair_value", "score_overall"} <= set(screener.columns)
    qa = demo_store.read("ops", "quality_report")
    assert not (qa["status"] == "error").any()
    runs = demo_store.read("ops", "pipeline_runs")
    assert runs["status"].iloc[-1] == "success"


def test_freshness_skips_api_calls(settings, demo_store):
    client = FakeFMPClient(today=date(2026, 11, 19))
    summary = run_ingestion(settings, demo_store, client)
    assert client.calls == 0
    assert summary.skipped_fresh > 0


def test_incremental_prices_use_from_param(settings, demo_store):
    seen = {}

    class Spy(FakeFMPClient):
        def get(self, path, params=None):
            if path == "historical-price-eod/full":
                seen[params["symbol"]] = params["from"]
            return super().get(path, params)

    run_ingestion(settings, demo_store, Spy(today=date(2026, 11, 19)), datasets=["prices_daily"], force=False,
                  tickers=["KO"])  # frais -> rien
    assert seen == {}
    # On vieillit l'état pour forcer un rafraîchissement incrémental
    state = demo_store.read("ops", "ingestion_state")
    state["last_success_at"] = pd.Timestamp("2000-01-01", tz="UTC")
    demo_store.write("ops", "ingestion_state", state)
    run_ingestion(settings, demo_store, Spy(today=date(2026, 11, 19)), datasets=["prices_daily"], tickers=["KO"])
    assert seen["KO"] > "2026-01-01"


def test_restricted_endpoint_is_skipped(settings, store):
    class Restricted(FakeFMPClient):
        def get(self, path, params=None):
            self.calls += 1
            if path == "insider-trading/search":
                raise PlanRestrictedError("plan")
            return super().get(path, params)

    client = Restricted(today=date(2026, 11, 19))
    result = run_pipeline(settings, store, client=client, force=True)
    assert "insider_trades" in result["ingest"]["restricted_datasets"]
    assert result["status"] == "success"
    state = store.read("ops", "ingestion_state")
    assert (state.loc[state["dataset"] == "insider_trades", "status"] == "restricted").sum() >= 1


def test_client_requires_key():
    with pytest.raises(FMPError):
        FMPClient(api_key="")


def test_client_plan_restriction(monkeypatch):
    client = FMPClient(api_key="x", requests_per_minute=1000)

    class Resp:
        status_code = 402
        text = "Restricted Endpoint"
        headers: dict = {}

    monkeypatch.setattr(client.session, "get", lambda *a, **k: Resp())
    with pytest.raises(PlanRestrictedError):
        client.get("profile", {"symbol": "KO"})


def test_quota_exceeded_stops_and_resumes(settings, store):
    class Quota(FakeFMPClient):
        budget = 30

        def get(self, path, params=None):
            if self.calls >= self.budget:
                raise QuotaExceededError("Limit Reach")
            return super().get(path, params)

    first = Quota(today=date(2026, 11, 19))
    s1 = run_ingestion(settings, store, first)
    assert s1.quota_exceeded and s1.fetched == 30
    second = FakeFMPClient(today=date(2026, 11, 19))
    s2 = run_ingestion(settings, store, second)
    assert s2.skipped_fresh >= 30 and not s2.quota_exceeded


def test_symbol_level_restriction_does_not_disable_dataset(settings, store):
    class SomeSymbols(FakeFMPClient):
        def get(self, path, params=None):
            if path == "quote" and params.get("symbol") == "KO":
                self.calls += 1
                raise PlanRestrictedError("Premium Query Parameter: symbol not available")
            return super().get(path, params)

    result = run_pipeline(settings, store, client=SomeSymbols(today=date(2026, 11, 19)), force=True)
    assert "quote" not in result["ingest"]["restricted_datasets"]
    assert result["ingest"]["restricted_symbols"]["quote"] == ["KO"]
    quote = store.read("silver", "quote")
    assert set(quote["symbol"]) == set(TEST_TICKERS) - {"KO"}
    val = store.read("gold", "valuation").set_index("symbol")
    assert val.loc["KO", "price"] > 0  # repli sur le prix du profil


def test_gold_without_financial_statements(settings, store):
    blocked = {"income-statement", "balance-sheet-statement", "cash-flow-statement", "ratios", "key-metrics"}

    class NoStatements(FakeFMPClient):
        def get(self, path, params=None):
            if path in blocked:
                self.calls += 1
                raise PlanRestrictedError("Restricted Endpoint")
            return super().get(path, params)

    result = run_pipeline(settings, store, client=NoStatements(today=date(2026, 11, 19)), force=True)
    assert result["status"] in ("success", "partial")
    assert "income_statement_annual" in result["ingest"]["restricted_datasets"]
    val = store.read("gold", "valuation")
    assert set(val["symbol"]) == set(TEST_TICKERS)
    assert store.read("gold", "graham_criteria").groupby("symbol").size().eq(7).all()
    assert not store.read("gold", "screener").empty


def test_basic_plan_caps_history_and_excludes_datasets(settings, store):
    import copy
    import dataclasses

    raw = copy.deepcopy(settings.raw)
    raw["api"]["plan"] = "basic"
    basic = dataclasses.replace(settings, raw=raw)
    assert basic.history["annual_statements_limit"] == 5
    seen = []

    class Spy(FakeFMPClient):
        def get(self, path, params=None):
            seen.append((path, dict(params or {})))
            return super().get(path, params)

    run_ingestion(basic, store, Spy(today=date(2026, 11, 19)), force=True)
    paths = {p for p, _ in seen}
    assert "news/stock" not in paths and "insider-trading/search" not in paths
    assert all(p.get("limit", 0) <= 5 for path, p in seen if path == "income-statement")
    assert all(p.get("period") != "quarter" for path, p in seen if path == "income-statement")
