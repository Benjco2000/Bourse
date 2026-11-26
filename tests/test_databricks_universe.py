import copy
import dataclasses
import io
from datetime import date
from types import SimpleNamespace

import pandas as pd

from bourse import config as config_mod
from bourse.core.normalize import build_ttm
from bourse.databricks import sync_volume_to_local
from bourse.fmp.fake import FakeFMPClient
from bourse.ingestion import resolve_universe, run_ingestion
from bourse.pipeline import run_pipeline
from bourse.storage import Store


def test_config_overlay(monkeypatch):
    monkeypatch.setenv("BOURSE_ENV", "databricks")
    config_mod.get_settings.cache_clear()
    try:
        s = config_mod.get_settings()
        assert s.section("ml")["registry"]["tracking_uri"] == "databricks"
        assert s.section("ml")["registry"]["model_name"] == "workspace.bourse.bourse_pfcf"
        assert s.section("ml")["target"]["max_pfcf"] == 150  # le reste de la config est conservé
    finally:
        monkeypatch.delenv("BOURSE_ENV")
        config_mod.get_settings.cache_clear()


def test_store_write_falls_back_when_rename_unsupported(tmp_path, monkeypatch):
    import bourse.storage as storage

    def no_rename(a, b):
        raise OSError("rename not supported")

    monkeypatch.setattr(storage.os, "replace", no_rename)
    s = Store(tmp_path)
    s.write("gold", "t", pd.DataFrame({"a": [1], "d": [pd.Timestamp("2024-01-01 10:00:00.123456789")]}))
    out = s.read("gold", "t")
    assert out["a"].tolist() == [1]
    assert not list((tmp_path / "gold").glob("*.tmp"))


def test_sync_volume_to_local(tmp_path):
    files = {"/V/gold/kpis.parquet": b"A", "/V/gold/ml_dataset.parquet": b"B", "/V/ops/data_mode.json": b"{}"}

    class Files:
        def list_directory_contents(self, path):
            items = [SimpleNamespace(path=p, name=p.rsplit("/", 1)[1], is_directory=False, file_size=len(v),
                                     last_modified=1) for p, v in files.items() if p.startswith(path + "/")]
            if not items:
                raise FileNotFoundError(path)
            return items

        def download(self, path):
            self.calls = getattr(self, "calls", 0) + 1
            return SimpleNamespace(contents=io.BytesIO(files[path]))

    client = SimpleNamespace(files=Files())
    assert sync_volume_to_local("/V", tmp_path, client=client) == 2  # ml_dataset exclu
    assert (tmp_path / "gold" / "kpis.parquet").read_bytes() == b"A"
    assert sync_volume_to_local("/V", tmp_path, client=client) == 0  # inchangé -> rien à télécharger


def test_extended_universe_gets_reduced_datasets(settings, store, tmp_path):
    csv = tmp_path / "u.csv"
    pd.DataFrame({"symbol": ["DM01", "DM02", "KO"]}).to_csv(csv, index=False)
    raw = copy.deepcopy(settings.raw)
    raw["universe"]["extended_universe_file"] = str(csv)
    s = dataclasses.replace(settings, raw=raw, tickers=["KO", "MSFT"])
    assert resolve_universe(s, store) == ["KO", "MSFT", "DM01", "DM02"]
    seen: dict[str, set] = {}

    class Spy(FakeFMPClient):
        def get(self, path, params=None):
            sym = (params or {}).get("symbol") or (params or {}).get("symbols")
            if sym:
                seen.setdefault(sym, set()).add(path)
            return super().get(path, params)

    run_ingestion(s, store, Spy(today=date(2026, 11, 19)), force=True)
    assert "news/stock" in seen["KO"] and "quote" in seen["KO"]
    assert "news/stock" not in seen["DM01"] and "quote" not in seen["DM01"]
    assert "income-statement" in seen["DM01"] and "historical-market-capitalization" in seen["DM01"]

    run_pipeline(s, store, steps=("silver", "core", "gold"))
    kpis = store.read("gold", "kpis")
    assert {"DM01", "DM02", "KO", "MSFT"} <= set(kpis["ticker"])
    peers = store.read("core", "peers")
    companies = store.read("core", "companies").set_index("ticker")
    auto = peers[peers["ticker"] == "DM01"]
    same_sector = (companies["sector"] == companies.loc["DM01", "sector"]).sum() > 1
    assert auto.empty == (not same_sector)
    assert auto["source"].str.startswith("auto:").all()
    assert (peers.loc[peers["ticker"] == "KO", "source"].str.startswith("config:")).all()


def test_vectorized_ttm_requires_complete_consecutive_quarters():
    q = pd.DataFrame({
        "ticker": "X", "period_type": "quarter",
        "period_end": pd.to_datetime(["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"]),
        "available_date": pd.to_datetime(["2024-04-30", "2024-07-30", "2024-10-30", "2025-01-30", "2025-04-30"]),
        "fiscal_year": 2024, "reported_currency": "USD", "revenue": [1.0, 2.0, None, 4.0, 5.0],
        "free_cash_flow": [1.0, 1.0, 1.0, 1.0, 1.0]})
    t = build_ttm(q)
    assert len(t) == 2
    assert t["revenue"].isna().all()  # un trimestre manquant -> pas de somme partielle
    assert t["free_cash_flow"].tolist() == [4.0, 4.0]
