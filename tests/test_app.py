"""Tests de fumée de l'application Streamlit sur les données de démonstration."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
PAGES = ["views/home.py", "views/stock.py", "views/screener.py", "views/watchlist.py", "views/news.py",
         "views/earnings.py", "views/pipeline.py"]


@pytest.fixture()
def demo_env(demo_store, monkeypatch):
    monkeypatch.setenv("BOURSE_DATA_DIR", str(demo_store.root))
    import streamlit as st

    st.cache_resource.clear()
    st.cache_data.clear()
    return demo_store


@pytest.mark.parametrize("page", PAGES)
def test_pages_render_without_error(demo_env, page):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    if page != PAGES[0]:
        at.switch_page(page)
        at.run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("symbol", ["KO", "AAPL"])
def test_stock_page(demo_env, symbol):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.query_params["symbol"] = symbol
    at.switch_page("views/stock.py")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert symbol in at.title[0].value
    assert len(at.tabs) == 9


@pytest.fixture()
def sparse_env(settings, store, monkeypatch):
    """Plan restreint : pas d'états financiers, ni news, et certains tickers refusés."""
    from datetime import date

    from bourse.fmp.client import PlanRestrictedError
    from bourse.fmp.fake import FakeFMPClient
    from bourse.pipeline import run_pipeline

    blocked = {"income-statement", "balance-sheet-statement", "cash-flow-statement", "ratios", "key-metrics",
               "dividends", "financial-scores", "news/stock", "insider-trading/search"}

    class Sparse(FakeFMPClient):
        def get(self, path, params=None):
            sym = (params or {}).get("symbol")
            if path in blocked or (path in ("quote", "historical-price-eod/full") and sym == "KO"):
                self.calls += 1
                raise PlanRestrictedError("Restricted")
            return super().get(path, params)

    run_pipeline(settings, store, client=Sparse(today=date(2026, 11, 19)), force=True)
    monkeypatch.setenv("BOURSE_DATA_DIR", str(store.root))
    import streamlit as st

    st.cache_resource.clear()
    st.cache_data.clear()
    return store


@pytest.mark.parametrize("symbol", ["KO", "MSFT"])
def test_stock_page_with_sparse_data(sparse_env, symbol):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.query_params["symbol"] = symbol
    at.switch_page("views/stock.py")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
