"""Lac de données ML (séparé des données de l'application) : ingestion de l'univers d'entraînement."""

from __future__ import annotations

import dataclasses
import logging

from ..config import Settings
from ..core.normalize import build_core
from ..ingestion import IngestionSummary, SupportsGet, run_ingestion
from ..storage import Store
from ..transform.silver import build_silver
from .universe import load_universe

log = logging.getLogger(__name__)


def ml_settings(settings: Settings) -> Settings:
    sub = settings.section("ml").get("data_subdir") or ""
    return dataclasses.replace(settings, data_dir=settings.data_dir / sub) if sub else settings


def ml_store(settings: Settings) -> Store:
    return Store(ml_settings(settings).data_dir)


def ingest_ml(settings: Settings, client: SupportsGet, limit: int | None = None,
              force: bool = False) -> IngestionSummary:
    tickers = load_universe(settings)
    if limit:
        tickers = tickers[:limit]
    datasets = list(settings.section("ml").get("datasets", []))
    mls = ml_settings(settings)
    store = Store(mls.data_dir)
    log.info("Ingestion ML : %d sociétés × %d jeux de données (≈ %d appels au premier chargement)",
             len(tickers), len(datasets), len(tickers) * len(datasets))
    summary = run_ingestion(mls, store, client, tickers=tickers, datasets=datasets, force=force)
    build_silver(store, datasets=datasets)
    from ..ingestion import resolve_universe

    build_core(mls, store, resolve_universe(mls, store) if mls is settings else tickers)
    return summary


def merge_legacy_lake(settings: Settings) -> dict[str, int]:
    """Fusionne l'ancien lac ML séparé (data/ml/bronze) dans le lac principal, sans nouvel appel API."""
    import shutil

    import pandas as pd

    old = settings.data_dir / "ml"
    if not (old / "bronze").exists():
        return {"files": 0}
    main = Store(settings.data_dir)
    copied = 0
    for src in (old / "bronze").rglob("*.json"):
        dst = settings.data_dir / "bronze" / src.relative_to(old / "bronze")
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
    old_state = Store(old).read("ops", "ingestion_state")
    if not old_state.empty:
        cur = main.read("ops", "ingestion_state")
        merged = pd.concat([cur, old_state], ignore_index=True) if not cur.empty else old_state
        merged = merged.sort_values("last_success_at").drop_duplicates(["dataset", "key"], keep="last")
        main.write("ops", "ingestion_state", merged)
    log.info("Lac ML fusionné : %d fichiers bronze copiés", copied)
    return {"files": copied, "state_rows": len(old_state)}
