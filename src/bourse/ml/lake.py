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
    return dataclasses.replace(settings, data_dir=settings.data_dir / settings.section("ml").get("data_subdir", "ml"))


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
    build_core(mls, store, tickers)
    return summary
