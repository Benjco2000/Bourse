"""« Déploiement » batch : application du modèle champion aux sociétés suivies -> gold.ml_predictions.

Exécuté par `bourse ml predict` et automatiquement à la fin de `bourse run` (si un champion existe), pour que
les estimations suivent l'évolution des cours et des publications. L'application lit cette table ; elle
charge aussi le modèle (`load_model`) pour le simulateur, sans jamais l'entraîner.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from ..financials.metrics import cap_bucket, price_from_multiple
from ..storage import Store, utcnow
from .dataset import CAT_FEATURES, KPI_FEATURES, features_as_of
from .registry import ModelBundle

log = logging.getLogger(__name__)
MIN_KPIS = 5


def predict_frame(bundle: ModelBundle, X: pd.DataFrame) -> pd.DataFrame:
    """Multiple estimé (bas / central / haut) pour des lignes de features."""
    pred = bundle.model.predict(X[KPI_FEATURES + CAT_FEATURES])
    q_lo, q_hi = bundle.meta.get("interval_log_quantiles", [math.log(0.7), math.log(1.3)])
    return pd.DataFrame({"pfcf_low": np.exp(pred + q_lo), "pfcf_mid": np.exp(pred), "pfcf_high": np.exp(pred + q_hi)},
                        index=X.index)


def contributions(bundle: ModelBundle, row: pd.DataFrame) -> dict[str, float]:
    """Effet de chaque KPI : multiple prédit − multiple prédit si ce KPI était à la médiane d'entraînement.
    Explication locale « toutes choses égales par ailleurs » : association apprise, pas causalité."""
    base = float(predict_frame(bundle, row)["pfcf_mid"].iloc[0])
    out = {}
    for f in KPI_FEATURES:
        med = bundle.meta.get("domain", {}).get(f, {}).get("median")
        if med is None or pd.isna(row[f].iloc[0]):
            continue
        alt = row.copy()
        alt[f] = med
        out[f] = base - float(predict_frame(bundle, alt)["pfcf_mid"].iloc[0])
    return out


def domain_check(bundle: ModelBundle, feats: dict[str, float], sector: str) -> list[str]:
    issues = []
    sectors = bundle.meta.get("sectors", {})
    if sector not in sectors:
        issues.append(f"secteur « {sector} » absent de l'entraînement")
    elif sectors[sector] < 50:
        issues.append(f"secteur « {sector} » peu représenté ({sectors[sector]} observations)")
    for f, v in feats.items():
        d = bundle.meta.get("domain", {}).get(f)
        if d and v is not None and not pd.isna(v) and not (d["p01"] <= v <= d["p99"]):
            issues.append(f"{f} hors de la plage d'entraînement")
    return issues


def run_predictions(settings: Settings, store: Store, bundle: ModelBundle) -> pd.DataFrame:
    st = store.read("core", "financial_statements")
    ttm = store.read("core", "ttm")
    mc = store.read("core", "market_cap_daily")
    companies = store.read("core", "companies")
    prices = store.read("core", "prices_daily")
    kpi_cfg = settings.section("kpis")
    buckets = settings.section("cap_buckets") or {}
    now = utcnow().replace(tzinfo=None)
    rows: list[dict[str, Any]] = []
    for c in companies.to_dict("records"):
        t_ = c["ticker"]
        st_t = st[st["ticker"] == t_]
        mc_t = mc[mc["ticker"] == t_].sort_values("date")
        rec: dict[str, Any] = {"ticker": t_, "model_version": bundle.version,
                               "model_trained_at": bundle.meta.get("trained_at"), "predicted_at": now,
                               "model_usable": bundle.usable}
        if st_t.empty or mc_t.empty:
            rows.append(rec | {"status": "insufficient_inputs", "status_reason": "états financiers ou "
                               "capitalisation indisponibles"})
            continue
        as_of = mc_t["date"].iloc[-1]
        cap = float(mc_t["market_cap"].iloc[-1])
        annual = st_t[st_t["period_type"] == "annual"].sort_values("fiscal_year")
        feats, ttm_row = features_as_of(annual, st_t, ttm[ttm["ticker"] == t_], t_, as_of, kpi_cfg)
        sector = c.get("sector") or "Inconnu"
        X = pd.DataFrame([{**feats, "sector": sector, "cap_bucket": cap_bucket(cap, buckets)}])
        p = predict_frame(bundle, X).iloc[0]
        fcf = float(ttm_row["free_cash_flow"]) if ttm_row is not None and pd.notna(ttm_row.get("free_cash_flow")) \
            else None
        shares = float(ttm_row["shares_diluted"]) if ttm_row is not None and pd.notna(ttm_row.get("shares_diluted")) \
            else None
        px_t = prices[prices["ticker"] == t_].sort_values("date") if not prices.empty else prices
        close = float(px_t["close"].iloc[-1]) if not px_t.empty else None
        n_kpis = sum(not pd.isna(v) for v in feats.values())
        issues = domain_check(bundle, feats, sector)
        theo = {k: price_from_multiple(p[f"pfcf_{k}"], fcf, shares) for k in ("low", "mid", "high")}
        if not bundle.usable:
            status, reason = "model_not_usable", "; ".join(bundle.meta.get("usability", {}).get("reasons", []))
        elif n_kpis < MIN_KPIS:
            status, reason = "insufficient_inputs", f"{n_kpis}/7 KPIs calculables (minimum {MIN_KPIS})"
        elif not theo["mid"].ok:
            status, reason = "not_applicable", theo["mid"].note
        elif len(issues) >= 2 or any("absent" in i for i in issues):
            status, reason = "out_of_domain", "; ".join(issues)
        else:
            status, reason = "ok", "; ".join(issues) or None
        rows.append(rec | {
            "status": status, "status_reason": reason, "as_of": as_of, "sector": sector,
            "market_cap": cap, "fcf_ttm": fcf, "shares_diluted": shares, "last_close": close,
            "current_pfcf": cap / fcf if fcf and fcf > 0 else None,
            "pfcf_low": float(p["pfcf_low"]), "pfcf_mid": float(p["pfcf_mid"]), "pfcf_high": float(p["pfcf_high"]),
            "price_low": theo["low"].value, "price_mid": theo["mid"].value, "price_high": theo["high"].value,
            "gap_vs_price": (theo["mid"].value / close - 1) if theo["mid"].ok and close else None,
            "n_kpis": n_kpis, "domain_issues": json.dumps(issues, ensure_ascii=False),
            "features": json.dumps({k: (None if pd.isna(v) else v) for k, v in feats.items()}),
            "contributions": json.dumps(contributions(bundle, X)),
        })
    df = pd.DataFrame(rows)
    store.write("gold", "ml_predictions", df)
    log.info("gold.ml_predictions : %d sociétés (modèle v%s, %s) — statuts : %s", len(df), bundle.version,
             "exploitable" if bundle.usable else "non exploitable", df["status"].value_counts().to_dict())
    return df
