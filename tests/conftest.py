import copy
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
    raw = copy.deepcopy(base.raw)
    raw.setdefault("api", {})["plan"] = "starter"  # couverture complète dans les tests
    # isole les tests de l'univers élargi réel (config/universe_ml.csv, ~1000 sociétés, versionné)
    raw.setdefault("universe", {})["extended_universe_file"] = str(tmp_path / "universe_ext.csv")
    raw.setdefault("ml", {})["universe_file"] = str(tmp_path / "universe_ml.csv")
    return dataclasses.replace(base, raw=raw, data_dir=tmp_path / "data", tickers=list(TEST_TICKERS))


@pytest.fixture()
def store(settings):
    return Store(settings.data_dir)


@pytest.fixture()
def demo_store(settings, store):
    run_pipeline(settings, store, client=FakeFMPClient(today=date(2026, 11, 19)), force=True, demo=True)
    return store
