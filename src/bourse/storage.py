"""Stockage local en couches (bronze JSON / silver & gold Parquet / ops).

Arborescence sous `data_dir` :

    bronze/<dataset>/ingest_date=YYYY-MM-DD/<SYMBOL|_market>.json   (réponses brutes de l'API)
    silver/<table>.parquet                                           (staging : schéma du fournisseur, typé, dédupliqué)
    core/<table>.parquet                                             (modèle normalisé indépendant du fournisseur)
    gold/<table>.parquet                                             (indicateurs métier prêts pour l'app)
    ops/<table>.parquet                                              (état d'ingestion, runs, qualité)

Les écritures sont atomiques (fichier temporaire puis rename) : l'application peut lire
pendant que le pipeline écrit. Toute la lecture/écriture passe par cette classe, ce qui
permettra de basculer vers Delta (Databricks) ou GCS/BigQuery sans toucher au reste.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

LAYERS = ("bronze", "silver", "core", "gold", "ops")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _replace(tmp: str, path: Path) -> None:
    """Renommage atomique ; repli par copie sur les systèmes de fichiers qui ne le permettent pas
    (certains montages FUSE, dont les Volumes Unity Catalog)."""
    try:
        os.replace(tmp, path)
    except OSError:
        shutil.copyfile(tmp, path)
        os.remove(tmp)


class Store:
    def __init__(self, data_dir: Path | str):
        self.root = Path(data_dir)
        for layer in LAYERS:
            (self.root / layer).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ bronze
    def bronze_path(self, dataset: str, key: str, ingest_date: date) -> Path:
        safe_key = key.replace("/", "_")
        return self.root / "bronze" / dataset / f"ingest_date={ingest_date.isoformat()}" / f"{safe_key}.json"

    def write_bronze(self, dataset: str, key: str, payload: Any, params: dict[str, Any],
                     fetched_at: datetime | None = None) -> Path:
        fetched_at = fetched_at or utcnow()
        path = self.bronze_path(dataset, key, fetched_at.date())
        envelope = {
            "dataset": dataset,
            "key": key,
            "fetched_at": fetched_at.isoformat(),
            "params": {k: v for k, v in params.items() if k != "apikey"},
            "payload": payload,
        }
        self._atomic_write_text(path, json.dumps(envelope, default=str))
        return path

    def iter_bronze(self, dataset: str, since: datetime | None = None) -> Iterable[dict[str, Any]]:
        """Itère sur les enveloppes bronze d'un dataset, triées par date de récupération."""
        base = self.root / "bronze" / dataset
        if not base.exists():
            return []
        envelopes = []
        partitions = [p for p in base.glob("ingest_date=*") if p.is_dir()]
        if since is not None:  # élagage par partition : on ne liste pas les fichiers des jours déjà traités
            partitions = [p for p in partitions if p.name.split("=", 1)[1] >= since.date().isoformat()]
        for path in (f for part in partitions for f in part.glob("*.json")):
            with open(path, encoding="utf-8") as fh:
                env = json.load(fh)
            if since is not None and datetime.fromisoformat(env["fetched_at"]) <= since:
                continue
            envelopes.append(env)
        envelopes.sort(key=lambda e: e["fetched_at"])
        return envelopes

    # --------------------------------------------------------- silver/gold/ops
    def table_path(self, layer: str, name: str) -> Path:
        assert layer in LAYERS, layer
        return self.root / layer / f"{name}.parquet"

    def exists(self, layer: str, name: str) -> bool:
        return self.table_path(layer, name).exists()

    def read(self, layer: str, name: str, columns: list[str] | None = None) -> pd.DataFrame:
        path = self.table_path(layer, name)
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path, columns=columns)

    def write(self, layer: str, name: str, df: pd.DataFrame) -> Path:
        path = self.table_path(layer, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        os.close(fd)
        try:
            # horodatages en microsecondes : lisibles par Spark / Delta (pas de nanosecondes)
            df.reset_index(drop=True).to_parquet(tmp, index=False, coerce_timestamps="us",
                                                 allow_truncated_timestamps=True)
            _replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return path

    def upsert(self, layer: str, name: str, new: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
        """Fusionne `new` dans la table : la dernière version d'une clé l'emporte."""
        if new.empty:
            return self.read(layer, name)
        existing = self.read(layer, name)
        combined = new if existing.empty else pd.concat([existing, new], ignore_index=True)
        usable_keys = [k for k in keys if k in combined.columns]
        if usable_keys:
            combined = combined.drop_duplicates(subset=usable_keys, keep="last")
        self.write(layer, name, combined)
        return combined

    def list_tables(self, layer: str) -> list[str]:
        return sorted(p.stem for p in (self.root / layer).glob("*.parquet"))

    # ------------------------------------------------------------------ utils
    def read_json(self, layer: str, name: str, default: Any = None) -> Any:
        path = self.root / layer / f"{name}.json"
        if not path.exists():
            return default
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def write_json(self, layer: str, name: str, obj: Any) -> None:
        self._atomic_write_text(self.root / layer / f"{name}.json", json.dumps(obj, indent=2, default=str))

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        _replace(tmp, path)
