import copy
import dataclasses
from datetime import date

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("mlflow")
pytest.importorskip("sklearn")

from bourse.config import get_settings  # noqa: E402
from bourse.fmp.fake import FakeFMPClient  # noqa: E402
from bourse.ml import registry  # noqa: E402
from bourse.ml.dataset import features_as_of, write_dataset  # noqa: E402
from bourse.ml.lake import ingest_ml, ml_store  # noqa: E402
from bourse.ml.predict import run_predictions  # noqa: E402
from bourse.ml.train import _choose, split, train  # noqa: E402
from bourse.ml.universe import build_universe, load_universe  # noqa: E402
from bourse.pipeline import run_pipeline  # noqa: E402
from bourse.storage import Store  # noqa: E402

TODAY = date(2026, 11, 19)


@pytest.fixture(scope="module")
def ml_env(tmp_path_factory):
    root = tmp_path_factory.mktemp("ml")
    raw = copy.deepcopy(get_settings().raw)
    raw["api"]["plan"] = "starter"
    raw["ml"]["universe_file"] = str(root / "universe.csv")
    raw["universe"]["extended_universe_file"] = str(root / "universe.csv")  # pas l'univers réel versionné
    raw["ml"]["universe"]["max_tickers"] = 30
    s = dataclasses.replace(get_settings(), raw=raw, data_dir=root / "data", tickers=["MSFT", "KO", "PEP", "AXP"])
    client = FakeFMPClient(today=TODAY)
    build_universe(s, client)
    ingest_ml(s, client, force=True)
    write_dataset(s, ml_store(s))
    app_store = Store(s.data_dir)
    run_pipeline(s, app_store, client=client, force=True, steps=("ingest", "silver", "core", "gold"))
    return s, app_store


def test_universe_excludes_banks_and_keeps_app_tickers(ml_env):
    s, _ = ml_env
    u = pd.read_csv(s.raw["ml"]["universe_file"])
    assert not u["industry"].str.contains("Bank").any()
    assert {"MSFT", "KO"} <= set(load_universe(s))


def test_dataset_is_point_in_time(ml_env):
    s, _ = ml_env
    store = ml_store(s)
    df = store.read("gold", "ml_dataset")
    assert len(df) > 200 and (df["pfcf"].between(1, 150)).all()
    assert np.allclose(np.log(df["pfcf"]), df["target"])
    assert (df["ttm_period_end"] < df["date"]).all()
    # Ajouter un exercice publié APRÈS t ne doit rien changer aux features à t
    st = store.read("core", "financial_statements")
    ttm = store.read("core", "ttm")
    t = pd.Timestamp("2020-06-30")
    g = st[st["ticker"] == "KO"]
    annual = g[g["period_type"] == "annual"].sort_values("fiscal_year")
    before, _ = features_as_of(annual, g, ttm[ttm["ticker"] == "KO"], "KO", t, s.section("kpis"))
    future = annual.iloc[[-1]].assign(fiscal_year=2030, revenue=1e15, available_date=pd.Timestamp("2031-01-01"),
                                      period_end=pd.Timestamp("2030-12-31"))
    annual2 = pd.concat([annual, future])
    after, _ = features_as_of(annual2, pd.concat([g, future]), ttm[ttm["ticker"] == "KO"], "KO", t,
                              s.section("kpis"))
    assert before == pytest.approx(after, nan_ok=True)


def test_split_is_chronological_with_unseen_companies(ml_env):
    s, _ = ml_env
    df = ml_store(s).read("gold", "ml_dataset")
    m = split(df, s.section("ml")["split"])
    assert not (m["train"] & m["val"]).any() and not (m["val"] & m["test"]).any()
    assert df.loc[m["train"], "date"].max() < df.loc[m["val"], "date"].min()
    assert df.loc[m["val"], "date"].max() < df.loc[m["test"], "date"].min()
    unseen = set(df.loc[m["test_unseen"], "ticker"])
    assert unseen and not unseen & set(df.loc[m["train"] | m["val"], "ticker"])


def test_choose_prefers_simpler_model_within_tolerance():
    scores = {"ridge": {"medae": 10.1}, "hist_gradient_boosting+segments": {"medae": 10.0},
              "random_forest": {"medae": 12.0}}
    assert _choose(scores, 0.02) == "ridge"
    assert _choose(scores, 0.0) == "hist_gradient_boosting+segments"


def test_train_register_promote_and_predict(ml_env):
    s, app_store = ml_env
    r1 = train(s, ml_store(s))
    assert r1["champion"] and r1["usability"]["usable"] is False  # 30 sociétés : non exploitable
    r2 = train(s, ml_store(s), promote=False)
    versions = registry.list_versions(s)
    assert [v["version"] for v in versions] == ["1", "2"]
    assert registry.load_model(s).version == "1"
    registry.promote(s, "2")
    bundle = registry.load_model(s)
    assert bundle.version == "2" and bundle.meta["selected_model"] == r2["selected"]
    preds = run_predictions(s, app_store, bundle)
    assert set(preds["status"]) == {"model_not_usable"}
    assert (preds["pfcf_mid"] > 0).all()

    # Modèle jugé exploitable (seuils abaissés pour le test) : prix théorique uniquement si FCF > 0
    raw = copy.deepcopy(s.raw)
    raw["ml"]["usability"] = {"min_train_obs": 50, "min_companies": 5, "must_beat_baseline": False}
    s2 = dataclasses.replace(s, raw=raw)
    train(s2, ml_store(s2), promote=True)
    preds = run_predictions(s2, app_store, registry.load_model(s2))
    ok = preds[preds["status"] == "ok"]
    assert not ok.empty
    assert np.allclose(ok["price_mid"], ok["pfcf_mid"] * ok["fcf_ttm"] / ok["shares_diluted"])
    assert preds.loc[preds["fcf_ttm"].fillna(-1) <= 0, "price_mid"].isna().all()


def test_ml_page_renders_with_model(ml_env, monkeypatch):
    from pathlib import Path

    import streamlit as st
    from streamlit.testing.v1 import AppTest

    s, app_store = ml_env
    if app_store.read("gold", "ml_predictions").empty:
        run_predictions(s, app_store, registry.load_model(s))
    monkeypatch.setenv("BOURSE_DATA_DIR", str(app_store.root))
    st.cache_resource.clear()
    st.cache_data.clear()
    app = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")
    for sym in ("KO", "MSFT"):
        at = AppTest.from_file(app, default_timeout=90)
        at.query_params["symbol"] = sym
        at.run()
        at.switch_page("views/ml.py")
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert len(at.tabs) == 2


def test_universe_handles_duplicate_exchange_fields(tmp_path):
    """Le screener FMP renvoie `exchange` (nom long) ET `exchangeShortName` : pas de colonnes dupliquées."""
    raw = copy.deepcopy(get_settings().raw)
    raw["ml"]["universe_file"] = str(tmp_path / "u.csv")
    s = dataclasses.replace(get_settings(), raw=raw)

    class Client:
        calls = 0

        def get(self, path, params=None):
            return [
                {"symbol": "AAA", "companyName": "A", "marketCap": 5e9, "sector": "Technology",
                 "industry": "Software", "exchange": "NASDAQ Global Select", "exchangeShortName": "NASDAQ"},
                {"symbol": "BBB", "companyName": "B", "marketCap": 9e9, "sector": "Financial Services",
                 "industry": "Banks - Regional", "exchange": "New York Stock Exchange", "exchangeShortName": "NYSE"},
                {"symbol": "CCC", "companyName": "C", "marketCap": 3e9, "sector": "Industrials",
                 "industry": "Aerospace", "exchange": "OTC", "exchangeShortName": "OTC"},
            ]

    df = build_universe(s, Client())
    assert df["symbol"].tolist() == ["AAA"]


def test_promotion_rule():
    from types import SimpleNamespace

    from bourse.ml.train import _should_promote

    def client(usable, medae, checked=True):
        params = {"monotonic_constraints": "{}"} if checked else {}
        run = SimpleNamespace(data=SimpleNamespace(tags={"usable": str(usable)}, metrics={"test_medae": medae},
                                                   params=params))
        return SimpleNamespace(get_run=lambda run_id: run)

    current = SimpleNamespace(run_id="r")
    assert _should_promote(None, None, {"medae": 9}, False)
    assert not _should_promote(client(True, 8), current, {"medae": 5}, False)   # jamais un modèle non exploitable
    assert _should_promote(client(False, 4), current, {"medae": 9}, True)       # exploitable remplace non exploitable
    assert _should_promote(client(True, 8), current, {"medae": 7}, True)
    assert not _should_promote(client(True, 8), current, {"medae": 9}, True)
    assert _should_promote(client(True, 5, checked=False), current, {"medae": 9}, True)  # ancien champion sans règles


def test_monotonic_constraints_hold_even_when_data_says_otherwise():
    from bourse.ml.dataset import KPI_FEATURES
    from bourse.ml.models import DEFAULT_MONOTONIC, make_candidates
    from bourse.ml.train import check_monotonicity

    rng = np.random.default_rng(0)
    n = 1500
    X = pd.DataFrame(rng.normal(size=(n, 7)), columns=KPI_FEATURES)
    X["sector"] = rng.choice(["A", "B"], n)
    X["cap_bucket"] = rng.choice(["large", "mega"], n)
    X.loc[rng.random(n) < 0.1, "fcf_cagr_5y"] = np.nan
    # relations « à l'envers » dans les données : croissance ↓ multiple, dilution ↑ multiple
    y = -2 * X["revenue_cagr_5y"] + 2 * X["share_change_5y"] - X["fcf_cagr_5y"].fillna(0) + rng.normal(size=n)
    domain = {f: {"p01": -2.3, "p99": 2.3, "median": 0.0} for f in KPI_FEATURES}
    for seg in (False, True):
        for name, model in make_candidates(seg).items():
            model.fit(X, y)
            viol = check_monotonicity(model, X, DEFAULT_MONOTONIC, domain)
            assert max(viol.values()) == 0, (name, viol)
