import dataclasses
from datetime import date

import pytest

from bourse.config import get_settings
from bourse.fmp.fake import FakeFMPClient
from bourse.pipeline import run_pipeline
from bourse.storage import Store

TEST_TICKERS = ["MSFT", "KO", "JNJ", "AAPL"]


@pytest.fixture()
def settings(tmp_path):
    base = get_settings()
    return dataclasses.replace(base, data_dir=tmp_path / "data", tickers=list(TEST_TICKERS))


@pytest.fixture()
def store(settings):
    return Store(settings.data_dir)


@pytest.fixture()
def demo_store(settings, store):
    run_pipeline(settings, store, client=FakeFMPClient(today=date(2026, 11, 19)), force=True, demo=True)
    return store
