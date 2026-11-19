"""Ingestion FMP -> couche bronze (JSON brut, partitionné par date d'ingestion).

Fonctionnalités "industrielles" :
- politique de fraîcheur par jeu de données (pas de re-téléchargement inutile -> économie de quota) ;
- chargement incrémental des cours (depuis la dernière date connue) ;
- table d'état `ops/ingestion_state` (dernier succès / erreur par dataset × ticker) -> reprise sur erreur ;
- endpoints hors plan détectés une fois puis ignorés pour le reste du run.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

import pandas as pd

from .config import Settings
from .fmp.client import FMPError, PlanRestrictedError, QuotaExceededError
from .fmp.datasets import DATASETS, DATASETS_BY_NAME, Dataset
from .storage import Store, utcnow

log = logging.getLogger(__name__)

MARKET_KEY = "_market"


class SupportsGet(Protocol):
    calls: int

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


@dataclass
class IngestionSummary:
    fetched: int = 0
    skipped_fresh: int = 0
    failed: int = 0
    restricted_datasets: set[str] = field(default_factory=set)
    restricted_symbols: dict[str, list[str]] = field(default_factory=dict)
    restriction_messages: dict[str, str] = field(default_factory=dict)
    ok_by_dataset: dict[str, int] = field(default_factory=dict)
    quota_exceeded: bool = False
    errors: list[str] = field(default_factory=list)
    api_calls: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched,
            "skipped_fresh": self.skipped_fresh,
            "failed": self.failed,
            "restricted_datasets": sorted(self.restricted_datasets),
            "restricted_symbols": {k: sorted(v) for k, v in self.restricted_symbols.items()},
            "quota_exceeded": self.quota_exceeded,
            "errors": self.errors[:50],
            "api_calls": self.api_calls,
        }


def resolve_universe(settings: Settings, store: Store, tickers: list[str] | None = None) -> list[str]:
    if tickers:
        return [t.upper() for t in tickers]
    universe = list(settings.tickers)
    if settings.section("universe").get("include_sp500"):
        sp = store.read("silver", "sp500_constituents")
        if not sp.empty and "symbol" in sp.columns:
            universe += sp["symbol"].dropna().astype(str).str.upper().tolist()
    return list(dict.fromkeys(universe))


def _payload_rows(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        hist = payload.get("historical")
        return len(hist) if isinstance(hist, list) else 1
    return 0


def run_ingestion(settings: Settings, store: Store, client: SupportsGet,
                  tickers: list[str] | None = None, datasets: list[str] | None = None,
                  force: bool = False) -> IngestionSummary:
    summary = IngestionSummary()
    excluded = settings.plan_preset.get("excluded_datasets", set())
    selected = [DATASETS_BY_NAME[d] for d in datasets] if datasets else \
        [d for d in DATASETS if d.name not in excluded]
    if excluded and not datasets:
        log.info("Plan FMP '%s' : jeux de données non inclus ignorés : %s", settings.plan, ", ".join(sorted(excluded)))
    cfg = settings.raw | {"history": settings.history}
    refresh = settings.refresh_hours
    now = utcnow()

    state = store.read("ops", "ingestion_state")
    last_success: dict[tuple[str, str], pd.Timestamp] = {}
    if not state.empty:
        ok = state.dropna(subset=["last_success_at"])
        last_success = {(r.dataset, r.key): pd.Timestamp(r.last_success_at) for r in ok.itertuples()}

    def is_fresh(ds: Dataset, key: str) -> bool:
        if force:
            return False
        ts = last_success.get((ds.name, key))
        if ts is None:
            return False
        max_age = timedelta(hours=float(refresh.get(ds.frequency, 20)))
        return (now - ts.to_pydatetime()) < max_age

    # 1) Datasets "marché" d'abord (le S&P 500 peut élargir l'univers)
    market = [d for d in selected if d.scope == "market"]
    per_symbol = [d for d in selected if d.scope == "symbol"]

    tasks: list[tuple[Dataset, str]] = []
    for ds in market:
        if is_fresh(ds, MARKET_KEY):
            summary.skipped_fresh += 1
        else:
            tasks.append((ds, MARKET_KEY))
    state_rows: list[dict[str, Any]] = []
    _execute(tasks, settings, store, client, cfg, summary, state_rows)

    if any(d.name == "sp500_constituents" for d in market):
        from .transform.silver import build_silver  # import local pour éviter un cycle

        build_silver(store, datasets=["sp500_constituents"])

    # 2) Datasets par ticker
    universe = resolve_universe(settings, store, tickers)
    last_dates: dict[str, dict[str, pd.Timestamp]] = {}
    if not force:
        for ds in per_symbol:
            if ds.incremental and store.exists("silver", ds.name):
                df = store.read("silver", ds.name, columns=["symbol", "date"])
                if not df.empty:
                    last_dates[ds.name] = df.groupby("symbol")["date"].max().to_dict()

    tasks = []
    for ds in per_symbol:
        for sym in universe:
            if is_fresh(ds, sym):
                summary.skipped_fresh += 1
            else:
                tasks.append((ds, sym))
    log.info("Ingestion : %d appels à effectuer (%d tickers, %d jeux de données), %d déjà frais",
             len(tasks), len(universe), len(per_symbol), summary.skipped_fresh)
    _execute(tasks, settings, store, client, cfg, summary, state_rows, last_dates)

    # 3) Mise à jour de l'état
    if state_rows:
        new_state = pd.DataFrame(state_rows)
        if not state.empty:
            # conserve le dernier succès connu si l'appel du jour a échoué
            merged = new_state.merge(state[["dataset", "key", "last_success_at"]], on=["dataset", "key"],
                                     how="left", suffixes=("", "_prev"))
            merged["last_success_at"] = merged["last_success_at"].fillna(merged["last_success_at_prev"])
            new_state = merged.drop(columns=["last_success_at_prev"])
        store.upsert("ops", "ingestion_state", new_state, keys=["dataset", "key"])

    for name, syms in summary.restricted_symbols.items():
        if name not in summary.restricted_datasets:
            log.warning("%s : tickers refusés par votre plan FMP : %s", name, ", ".join(sorted(syms)))
    summary.api_calls = getattr(client, "calls", 0)
    log.info("Ingestion terminée : %d récupérés, %d déjà frais, %d erreurs, %d appels API%s",
             summary.fetched, summary.skipped_fresh, summary.failed, summary.api_calls,
             " — QUOTA ATTEINT" if summary.quota_exceeded else "")
    return summary


def _execute(tasks: list[tuple[Dataset, str]], settings: Settings, store: Store, client: SupportsGet,
             cfg: dict[str, Any], summary: IngestionSummary, state_rows: list[dict[str, Any]],
             last_dates: dict[str, dict[str, pd.Timestamp]] | None = None) -> None:
    if not tasks:
        return
    lock = threading.Lock()
    last_dates = last_dates or {}

    def work(ds: Dataset, key: str) -> None:
        with lock:
            if ds.name in summary.restricted_datasets or summary.quota_exceeded:
                return
        params = dict(ds.params(cfg))
        if ds.scope == "symbol":
            params[ds.symbol_param] = key
            known = last_dates.get(ds.name, {})
            if ds.incremental and key in known:
                params["from"] = (pd.Timestamp(known[key]) - pd.Timedelta(days=7)).date().isoformat()
        fetched_at = utcnow()
        row: dict[str, Any] = {"dataset": ds.name, "key": key, "last_attempt_at": fetched_at,
                               "last_success_at": pd.NaT, "status": "ok", "error": None, "rows": 0}
        try:
            payload = client.get(ds.path, params)
            store.write_bronze(ds.name, key, payload, params, fetched_at)
            row["last_success_at"] = fetched_at
            row["rows"] = _payload_rows(payload)
            with lock:
                summary.fetched += 1
                summary.ok_by_dataset[ds.name] = summary.ok_by_dataset.get(ds.name, 0) + 1
        except QuotaExceededError as exc:
            with lock:
                if not summary.quota_exceeded:
                    log.error("Quota FMP atteint : arrêt de l'ingestion. Relancez demain, le pipeline reprendra "
                              "là où il s'est arrêté (%s)", exc)
                summary.quota_exceeded = True
            return
        except PlanRestrictedError as exc:
            # Le plan peut refuser un endpoint entier OU seulement certains tickers (plan gratuit : ~87 tickers).
            # On ne désactive le dataset entier qu'après 3 refus sans aucun succès.
            row.update(status="restricted", error=str(exc)[:300])
            with lock:
                refused = summary.restricted_symbols.setdefault(ds.name, [])
                refused.append(key)
                if ds.name not in summary.restriction_messages:
                    summary.restriction_messages[ds.name] = str(exc)[:300]
                    log.warning("Accès refusé par FMP pour %s/%s : %s", ds.name, key, str(exc)[:200])
                if (len(refused) >= 3 and not summary.ok_by_dataset.get(ds.name)
                        and ds.name not in summary.restricted_datasets):
                    summary.restricted_datasets.add(ds.name)
                    log.warning("Endpoint non disponible avec votre plan, ignoré pour ce run : %s (%s)",
                                ds.name, ds.path)
        except (FMPError, ValueError) as exc:
            row.update(status="error", error=str(exc)[:300])
            with lock:
                summary.failed += 1
                summary.errors.append(f"{ds.name}/{key}: {exc}")
            log.warning("Échec %s/%s : %s", ds.name, key, exc)
        with lock:
            state_rows.append(row)

    workers = int(settings.api.get("max_workers", 8))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, ds, key) for ds, key in tasks]
        for i, fut in enumerate(as_completed(futures), 1):
            fut.result()
            if i % 100 == 0:
                log.info("  ... %d/%d", i, len(futures))
