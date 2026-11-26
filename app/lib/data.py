"""Accès aux données pour l'application (lecture seule des couches silver/gold/ops).

Le cache Streamlit est invalidé automatiquement quand le fichier Parquet change (mtime dans la clé).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from bourse.config import PROJECT_ROOT


@st.cache_data(ttl=1800, show_spinner="Synchronisation des données depuis Databricks…")
def sync_from_volume() -> int:
    """Databricks Apps : copie locale des tables du Volume (pas de montage /Volumes dans les apps).
    Rafraîchie toutes les 30 min ; ne fait rien en local (variable BOURSE_VOLUME_PATH absente)."""
    volume = os.getenv("BOURSE_VOLUME_PATH")
    if not volume:
        return 0
    from bourse.databricks import sync_volume_to_local

    return sync_volume_to_local(volume, os.getenv("BOURSE_DATA_DIR", "/tmp/bourse_data"))


@st.cache_resource
def data_location() -> tuple[Path, bool]:
    """(dossier de données, est_demo). Priorité : BOURSE_DATA_DIR > data/ > data_demo/."""
    env = os.getenv("BOURSE_DATA_DIR")
    candidates = [Path(env)] if env else [PROJECT_ROOT / "data", PROJECT_ROOT / "data_demo"]
    chosen = next((c for c in candidates if (c / "gold" / "screener.parquet").exists()), candidates[0])
    mode_file = chosen / "ops" / "data_mode.json"
    demo = False
    if mode_file.exists():
        demo = bool(json.loads(mode_file.read_text()).get("demo"))
    return chosen.resolve(), demo


def _path(layer: str, name: str) -> Path:
    return data_location()[0] / layer / f"{name}.parquet"


def _mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_data(ttl=600, show_spinner=False)
def _read(path_str: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path_str) if mtime else pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def _read_symbol(path_str: str, mtime: float, symbol: str, symbol_col: str) -> pd.DataFrame:
    if not mtime:
        return pd.DataFrame()
    con = duckdb.connect()
    try:
        return con.execute(f"SELECT * FROM read_parquet(?) WHERE {symbol_col} = ?", [path_str, symbol]).df()
    finally:
        con.close()


def table(layer: str, name: str) -> pd.DataFrame:
    p = _path(layer, name)
    return _read(str(p), _mtime(p)).copy()


def for_symbol(layer: str, name: str, symbol: str, symbol_col: str = "symbol") -> pd.DataFrame:
    p = _path(layer, name)
    if not p.exists():
        return pd.DataFrame()
    df = _read_symbol(str(p), _mtime(p), symbol, symbol_col)
    return df.copy()


def companies() -> pd.DataFrame:
    """Sociétés de l'univers (core), triées par ticker ; repli sur le screener pour les anciennes données."""
    df = table("core", "companies")
    if df.empty:
        sc = screener()
        if sc.empty:
            return pd.DataFrame(columns=["ticker", "name"])
        df = sc.rename(columns={"symbol": "ticker"})[["ticker", "name"]]
    return df.sort_values("ticker").reset_index(drop=True)


def get_peer_override(ticker: str) -> list[str] | None:
    f = data_location()[0] / "app" / "peer_overrides.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text()).get(ticker)
    except (ValueError, OSError):
        return None


def set_peer_override(ticker: str, peers: list[str] | None) -> None:
    f = data_location()[0] / "app" / "peer_overrides.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        current = {}
    if peers is None:
        current.pop(ticker, None)
    else:
        current[ticker] = peers
    f.write_text(json.dumps(current, indent=2))


def screener() -> pd.DataFrame:
    return table("gold", "screener")


def last_update() -> pd.Timestamp | None:
    runs = table("ops", "pipeline_runs")
    if runs.empty:
        return None
    ok = runs[runs["status"].isin(["success", "partial"])]
    return ok["ended_at"].max() if not ok.empty else None


# ------------------------------------------------------------------ watchlist
def _watchlist_file() -> Path:
    return data_location()[0] / "app" / "watchlist.json"


def get_watchlist() -> list[str]:
    f = _watchlist_file()
    if not f.exists():
        return []
    try:
        return list(json.loads(f.read_text()))
    except (ValueError, OSError):
        return []


def set_watchlist(symbols: list[str]) -> None:
    f = _watchlist_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sorted(set(symbols))))


def demo_banner() -> None:
    if data_location()[1]:
        st.warning("**Mode démonstration** — les données affichées sont synthétiques et n'ont aucune valeur "
                   "financière. Lancez `bourse run` avec votre clé FMP pour charger les vraies données.",
                   icon=":material/science:")
