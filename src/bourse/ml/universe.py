"""Univers d'entraînement ML : grandes capitalisations US via le screener FMP (ou un CSV fourni)."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from ..config import PROJECT_ROOT, Settings
from ..fmp.client import FMPError, PlanRestrictedError
from ..ingestion import SupportsGet

log = logging.getLogger(__name__)


def universe_path(settings: Settings) -> Path:
    p = Path(settings.section("ml").get("universe_file", "config/universe_ml.csv"))
    return p if p.is_absolute() else PROJECT_ROOT / p


def build_universe(settings: Settings, client: SupportsGet, path: Path | None = None) -> pd.DataFrame:
    """Interroge `company-screener` (actions actives, hors ETF/fonds), filtre et garde les plus grosses."""
    cfg = settings.section("ml").get("universe", {})
    params = {"country": cfg.get("country", "US"), "isEtf": "false", "isFund": "false",
              "isActivelyTrading": "true", "marketCapMoreThan": int(float(cfg.get("min_market_cap", 2e9))),
              "limit": 5000}
    try:
        rows = client.get("company-screener", params)
    except (PlanRestrictedError, FMPError) as exc:
        raise FMPError(
            f"Screener FMP indisponible ({exc}). Fournissez un CSV avec une colonne 'symbol' dans "
            f"{universe_path(settings)} (par ex. la liste du S&P 500 / Nasdaq-100)."
        ) from exc
    df = pd.DataFrame(rows)
    if df.empty:
        raise FMPError("Le screener n'a renvoyé aucune société.")
    df = df.rename(columns={"companyName": "name", "marketCap": "market_cap",
                            "exchangeShortName": "exchange"})
    exchanges = set(cfg.get("exchanges") or [])
    if exchanges and "exchange" in df.columns:
        df = df[df["exchange"].isin(exchanges)]
    keywords = [k.lower() for k in cfg.get("exclude_industry_keywords", [])]
    industry = df.get("industry", pd.Series("", index=df.index)).fillna("").str.lower()
    excluded = industry.map(lambda i: any(k in i for k in keywords))
    log.info("Univers ML : %d sociétés exclues par secteur d'activité (%s)", int(excluded.sum()),
             ", ".join(cfg.get("exclude_industry_keywords", [])))
    df = df[~excluded].sort_values("market_cap", ascending=False).head(int(cfg.get("max_tickers", 1000)))
    keep = [c for c in ("symbol", "name", "sector", "industry", "exchange", "market_cap") if c in df.columns]
    df = df[keep].reset_index(drop=True)
    out = path or universe_path(settings)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    log.info("Univers ML : %d sociétés écrites dans %s", len(df), out)
    return df


def load_universe(settings: Settings, path: Path | None = None) -> list[str]:
    p = path or universe_path(settings)
    if not p.exists():
        raise FileNotFoundError(f"{p} introuvable : lancez `bourse ml universe` (ou fournissez ce CSV).")
    df = pd.read_csv(p)
    col = "symbol" if "symbol" in df.columns else df.columns[0]
    tickers = df[col].dropna().astype(str).str.strip().str.upper().tolist()
    # les sociétés suivies dans l'application font toujours partie de l'univers ML
    return list(dict.fromkeys(tickers + list(settings.tickers)))
