"""Intégration Databricks : amorçage des jobs, publication en tables Unity Catalog, synchro pour l'app.

Le code métier reste identique en local et sur Databricks : seules changent les variables d'environnement
(`BOURSE_ENV=databricks`, `BOURSE_DATA_DIR=/Volumes/...`) et la provenance des secrets.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT = {"catalog": "workspace", "schema": "bourse", "volume": "lake", "scope": "bourse"}
SECRETS = {"FMP_API_KEY": "fmp-api-key", "OPENAI_API_KEY": "openai-api-key", "ANTHROPIC_API_KEY": "anthropic-api-key"}
PUBLISHED_TABLES = ["company_snapshot", "kpis", "ml_predictions", "valuation_history", "screener",
                    "news_synthesis", "fundamentals_annual"]
APP_SYNC_EXCLUDE = {"silver/market_cap_history.parquet", "gold/ml_dataset.parquet"}


def volume_path(catalog: str = DEFAULT["catalog"], schema: str = DEFAULT["schema"],
                volume: str = DEFAULT["volume"]) -> str:
    return f"/Volumes/{catalog}/{schema}/{volume}"


def bootstrap(catalog: str = DEFAULT["catalog"], schema: str = DEFAULT["schema"], volume: str = DEFAULT["volume"],
              scope: str = DEFAULT["scope"]):
    """À appeler en tête de chaque notebook de job : environnement, secrets, logs."""
    os.environ.setdefault("BOURSE_ENV", "databricks")
    os.environ.setdefault("BOURSE_DATA_DIR", volume_path(catalog, schema, volume))
    try:
        from pyspark.dbutils import DBUtils  # type: ignore
        from pyspark.sql import SparkSession  # type: ignore

        dbutils = DBUtils(SparkSession.getActiveSession())
        for env, key in SECRETS.items():
            if os.getenv(env):
                continue
            try:
                os.environ[env] = dbutils.secrets.get(scope, key)
            except Exception:  # secret absent : fonctionnalité correspondante désactivée
                pass
    except ImportError:
        log.warning("dbutils indisponible : hors Databricks, les secrets viennent de .env")
    from .config import get_settings, setup_logging

    get_settings.cache_clear()
    setup_logging()
    settings = get_settings()
    log.info("Bourse sur Databricks : données dans %s, clé FMP %s, clé LLM %s", settings.data_dir,
             "présente" if settings.api_key else "ABSENTE",
             "présente" if os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY") else "absente")
    return settings


def run_cli(*args: str) -> None:
    """Exécute une commande `bourse ...` et lève une exception en cas d'échec (le job passe en erreur)."""
    from .cli import main

    code = main(list(args))
    if code:
        raise RuntimeError(f"`bourse {' '.join(args)}` a échoué (code {code})")


def publish_tables(settings, catalog: str = DEFAULT["catalog"], schema: str = DEFAULT["schema"],
                   tables: list[str] | None = None) -> list[str]:
    """Publie les tables gold en tables Delta Unity Catalog (SQL, tableaux de bord, Genie)."""
    from pyspark.sql import SparkSession  # type: ignore

    spark = SparkSession.getActiveSession()
    done = []
    for name in tables or PUBLISHED_TABLES:
        path = Path(settings.data_dir) / "gold" / f"{name}.parquet"
        if not path.exists():
            continue
        df = spark.read.parquet(str(path))
        df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{catalog}.{schema}.{name}")
        done.append(name)
    log.info("Tables publiées dans %s.%s : %s", catalog, schema, ", ".join(done))
    return done


def sync_volume_to_local(volume: str, local_dir: str | Path, client: Any = None,
                         layers: tuple[str, ...] = ("silver", "core", "gold", "ops", "app")) -> int:
    """Copie (incrémentale) des tables du Volume vers le disque local de l'application Databricks Apps,
    qui n'a pas accès au montage /Volumes. Utilise l'API Files (SDK Databricks)."""
    if client is None:
        from databricks.sdk import WorkspaceClient  # type: ignore

        client = WorkspaceClient()
    local = Path(local_dir)
    manifest_path = local / ".sync_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    downloaded = 0
    for layer in layers:
        try:
            entries = list(client.files.list_directory_contents(f"{volume.rstrip('/')}/{layer}"))
        except Exception as exc:  # couche absente
            log.debug("Synchro : %s ignoré (%s)", layer, exc)
            continue
        for e in entries:
            if getattr(e, "is_directory", False):
                continue
            rel = f"{layer}/{e.name}"
            if rel in APP_SYNC_EXCLUDE or not (e.name.endswith(".parquet") or e.name.endswith(".json")):
                continue
            stamp = f"{getattr(e, 'last_modified', '')}-{getattr(e, 'file_size', '')}"
            target = local / rel
            if manifest.get(rel) == stamp and target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            content = client.files.download(e.path).contents.read()
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(content)
            os.replace(tmp, target)
            manifest[rel] = stamp
            downloaded += 1
    local.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest))
    log.info("Synchro Volume -> %s : %d fichier(s) mis à jour", local, downloaded)
    return downloaded
