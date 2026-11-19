"""Bronze -> Silver : aplatissement, snake_case, typage, déduplication, upsert incrémental.

Un "watermark" par dataset (date de récupération du dernier fichier bronze traité) rend la
transformation incrémentale et idempotente. `full_refresh=True` reconstruit depuis tout le bronze.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import pandas as pd

from ..fmp.datasets import DATASETS, DATASETS_BY_NAME, Dataset
from ..storage import Store

log = logging.getLogger(__name__)

_CAMEL_1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_2 = re.compile(r"([a-z0-9])([A-Z])")
DATE_COLUMNS_EXTRA = {"last_updated", "timestamp_date"}


def snake(name: str) -> str:
    name = name.strip().replace(" ", "_").replace("-", "_").replace(".", "_")
    name = _CAMEL_2.sub(r"\1_\2", _CAMEL_1.sub(r"\1_\2", name))
    return re.sub(r"_+", "_", name).lower()


def payload_to_rows(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = [r for r in payload if isinstance(r, dict)]
    elif isinstance(payload, dict):
        if isinstance(payload.get("historical"), list):  # ancien format v3
            sym = payload.get("symbol", key)
            rows = [{**r, "symbol": r.get("symbol", sym)} for r in payload["historical"]]
        else:
            rows = [payload]
    else:
        rows = []
    return rows


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.rename(columns={c: snake(c) for c in df.columns})
    df = df.loc[:, ~df.columns.duplicated()]
    for col in df.columns:
        if col == "date" or col.endswith("_date") or col in DATE_COLUMNS_EXTRA:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True).dt.tz_localize(None)
        elif df[col].dtype == object:
            non_null = df[col].dropna()
            if non_null.empty:
                continue
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            elif not all(isinstance(v, str) for v in non_null):
                df[col] = df[col].map(lambda v: None if v is None else str(v))
    return df


def envelopes_to_frame(ds: Dataset, envelopes: list[dict[str, Any]]) -> pd.DataFrame:
    frames = []
    for env in envelopes:
        rows = payload_to_rows(env["payload"], env["key"])
        if not rows:
            continue
        frame = pd.DataFrame(rows)
        if ds.scope == "symbol" and "symbol" not in frame.columns:
            frame["symbol"] = env["key"]
        if ds.name == "stock_news":
            frame["query_symbol"] = env["key"]
        frame["_fetched_at"] = env["fetched_at"]
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    df = normalize(pd.concat(frames, ignore_index=True))
    df["_fetched_at"] = pd.to_datetime(df["_fetched_at"], utc=True).dt.tz_localize(None)
    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).str.upper()
    keys = [k for k in ds.keys if k in df.columns]
    if keys:
        df = df.sort_values("_fetched_at").drop_duplicates(subset=keys, keep="last")
    return df


def _merge(existing: pd.DataFrame, new: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Upsert en mémoire (la nouvelle version d'une clé l'emporte), avec alignement des types."""
    if existing.empty:
        return new
    for col in set(existing.columns) & set(new.columns):
        a, b = existing[col].dtype, new[col].dtype
        if a == b:
            continue
        if pd.api.types.is_datetime64_any_dtype(a) or pd.api.types.is_datetime64_any_dtype(b):
            existing[col] = pd.to_datetime(existing[col], errors="coerce")
            new[col] = pd.to_datetime(new[col], errors="coerce")
        elif pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            existing[col] = existing[col].astype(float)
            new[col] = new[col].astype(float)
        else:
            existing[col] = existing[col].map(lambda v: None if pd.isna(v) else str(v))
            new[col] = new[col].map(lambda v: None if pd.isna(v) else str(v))
    combined = pd.concat([existing, new], ignore_index=True)
    usable = [k for k in keys if k in combined.columns]
    if usable:
        combined = combined.drop_duplicates(subset=usable, keep="last")
    return combined


def build_silver(store: Store, datasets: list[str] | None = None, full_refresh: bool = False) -> dict[str, int]:
    selected = [DATASETS_BY_NAME[d] for d in datasets] if datasets else list(DATASETS)
    watermarks: dict[str, str] = store.read_json("ops", "silver_watermarks", default={}) or {}
    result: dict[str, int] = {}

    for ds in selected:
        since = None if full_refresh or ds.mode == "replace" else watermarks.get(ds.name)
        envelopes = store.iter_bronze(ds.name, since=datetime.fromisoformat(since) if since else None)
        if not envelopes:
            continue
        if ds.mode == "replace":
            latest = max(e["fetched_at"] for e in envelopes)
            envelopes = [e for e in envelopes if e["fetched_at"] == latest]

        df = envelopes_to_frame(ds, envelopes)
        if ds.mode == "replace" or full_refresh:
            store.write("silver", ds.name, df)
            table = df
        else:
            existing = store.read("silver", ds.name)
            table = _merge(existing, df, list(ds.keys))
            store.write("silver", ds.name, table)
        watermarks[ds.name] = max(e["fetched_at"] for e in envelopes)
        result[ds.name] = len(table)
        log.info("silver.%-26s %6d lignes (+%d traitées)", ds.name, len(table), len(df))

    store.write_json("ops", "silver_watermarks", watermarks)
    return result
