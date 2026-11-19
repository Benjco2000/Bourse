"""Chargement de la configuration (fichier YAML + variables d'environnement / .env)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")
load_dotenv()  # .env du répertoire courant, si différent


@dataclass
class Settings:
    raw: dict[str, Any]
    data_dir: Path
    api_key: str | None
    config_path: Path
    tickers: list[str] = field(default_factory=list)

    # Raccourcis pratiques
    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {}) or {}

    @property
    def api(self) -> dict[str, Any]:
        return self.section("api")

    @property
    def valuation(self) -> dict[str, Any]:
        return self.section("valuation")

    @property
    def graham(self) -> dict[str, Any]:
        return self.section("graham")

    @property
    def history(self) -> dict[str, Any]:
        return self.section("history")

    @property
    def refresh_hours(self) -> dict[str, Any]:
        return self.section("refresh_hours")


def _read_secret_fallbacks() -> str | None:
    """Clé API : variable d'environnement, sinon secret Databricks / Streamlit si disponibles."""
    key = os.getenv("FMP_API_KEY")
    if key:
        return key.strip()
    # Databricks (notebook / job) : dbutils.secrets
    try:  # pragma: no cover - uniquement sur Databricks
        from pyspark.dbutils import DBUtils  # type: ignore
        from pyspark.sql import SparkSession  # type: ignore

        spark = SparkSession.getActiveSession()
        if spark is not None:
            scope = os.getenv("FMP_SECRET_SCOPE", "fmp-scope")
            secret_key = os.getenv("FMP_SECRET_KEY", "fmp-api-key")
            return DBUtils(spark).secrets.get(scope, secret_key)
    except Exception:
        pass
    return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    config_path = Path(os.getenv("BOURSE_CONFIG", PROJECT_ROOT / "config" / "settings.yaml"))
    with open(config_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    data_dir = Path(os.getenv("BOURSE_DATA_DIR", PROJECT_ROOT / "data")).resolve()
    tickers = [t.strip().upper() for t in (raw.get("universe", {}) or {}).get("tickers", []) if t]

    return Settings(
        raw=raw,
        data_dir=data_dir,
        api_key=_read_secret_fallbacks(),
        config_path=config_path,
        tickers=list(dict.fromkeys(tickers)),
    )


def setup_logging() -> None:
    level = os.getenv("BOURSE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
