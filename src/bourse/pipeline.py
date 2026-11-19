"""Orchestration : ingestion -> silver -> gold -> qualité, avec journal des runs (ops/pipeline_runs)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import pandas as pd

from .config import Settings
from .core.normalize import build_core
from .fmp.client import FMPClient
from .ingestion import SupportsGet, resolve_universe, run_ingestion
from .quality import run_quality_checks
from .storage import Store, utcnow
from .transform.gold import build_gold
from .transform.kpis import build_kpis
from .transform.silver import build_silver

log = logging.getLogger(__name__)

ALL_STEPS = ("ingest", "silver", "core", "gold", "quality")


def make_client(settings: Settings) -> FMPClient:
    api = settings.api
    return FMPClient(
        api_key=settings.api_key or "",
        base_url=api.get("base_url", "https://financialmodelingprep.com/stable"),
        requests_per_minute=int(api.get("requests_per_minute", 250)),
        timeout=float(api.get("timeout_seconds", 30)),
        max_retries=int(api.get("max_retries", 4)),
    )


def run_pipeline(settings: Settings, store: Store, client: SupportsGet | None = None,
                 steps: tuple[str, ...] = ALL_STEPS, tickers: list[str] | None = None,
                 datasets: list[str] | None = None, force: bool = False, full_refresh: bool = False,
                 demo: bool = False) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    started = utcnow()
    t0 = time.monotonic()
    summary: dict[str, Any] = {}
    status = "success"
    log.info("=== Run %s — étapes : %s ===", run_id, ", ".join(steps))
    try:
        if "ingest" in steps:
            client = client or make_client(settings)
            summary["ingest"] = run_ingestion(settings, store, client, tickers=tickers, datasets=datasets,
                                              force=force).as_dict()
            if summary["ingest"]["failed"] or summary["ingest"]["quota_exceeded"]:
                status = "partial"
        if "silver" in steps:
            summary["silver"] = build_silver(store, datasets=datasets, full_refresh=full_refresh)
        if "core" in steps:
            summary["core"] = build_core(settings, store, resolve_universe(settings, store, tickers))
        if "gold" in steps:
            summary["gold"] = build_gold(settings, store)
            summary["gold"]["kpis"] = build_kpis(settings, store)
        if "quality" in steps:
            qa = run_quality_checks(settings, store, run_id)
            summary["quality"] = qa["status"].value_counts().to_dict()
            if (qa["status"] == "error").any():
                status = "partial" if status == "success" else status
    except Exception as exc:
        status = "failed"
        summary["error"] = str(exc)
        log.exception("Run %s en échec", run_id)
        raise
    finally:
        run = pd.DataFrame([{
            "run_id": run_id, "started_at": started.replace(tzinfo=None),
            "ended_at": utcnow().replace(tzinfo=None), "duration_s": round(time.monotonic() - t0, 1),
            "status": status, "steps": ",".join(steps), "demo": demo,
            "summary": json.dumps(summary, default=str),
        }])
        store.upsert("ops", "pipeline_runs", run, keys=["run_id"])
        store.write_json("ops", "data_mode", {"demo": demo, "updated_at": utcnow().isoformat()})
        log.info("=== Run %s : %s en %.1fs ===", run_id, status, time.monotonic() - t0)
    return {"run_id": run_id, "status": status, **summary}
