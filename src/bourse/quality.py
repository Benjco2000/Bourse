"""Contrôles qualité exécutés à la fin de chaque run (résultats dans ops/quality_report)."""

from __future__ import annotations

import logging

import pandas as pd

from .config import Settings
from .fmp.datasets import DATASETS
from .ingestion import resolve_universe
from .storage import Store

log = logging.getLogger(__name__)


def run_quality_checks(settings: Settings, store: Store, run_id: str = "") -> pd.DataFrame:
    rows: list[dict] = []

    def add(check: str, status: str, detail: str) -> None:
        rows.append({"run_id": run_id, "check": check, "status": status, "detail": detail})

    universe = resolve_universe(settings, store)
    valuation = store.read("gold", "valuation")

    # 1. Couverture de l'univers
    covered = set(valuation["symbol"]) if not valuation.empty else set()
    missing = [s for s in universe if s not in covered]
    add("Couverture univers", "ok" if not missing else ("error" if len(missing) == len(universe) else "warn"),
        f"{len(universe) - len(missing)}/{len(universe)} tickers valorisés"
        + (f" — manquants : {', '.join(missing[:15])}" if missing else ""))

    # 2. Prix renseignés
    if not valuation.empty:
        no_price = valuation.loc[valuation["price"].isna(), "symbol"].tolist()
        add("Prix disponibles", "ok" if not no_price else "warn",
            "tous les tickers ont un prix" if not no_price else f"sans prix : {', '.join(no_price[:15])}")
        coverage = valuation["fair_value"].notna().mean()
        add("Juste valeur calculée", "ok" if coverage >= 0.7 else "warn", f"{coverage:.0%} des tickers")

    # 3. Fraîcheur des cours
    prices = store.read("silver", "prices_daily", columns=["symbol", "date"])
    if prices.empty:
        add("Fraîcheur des cours", "error", "aucun historique de cours")
    else:
        # cours quotidiens = sociétés suivies uniquement (l'univers élargi n'a que la capitalisation historique) ;
        # les anciens tickers encore présents en silver ne sont pas signalés
        tracked = set(settings.tickers)
        last = prices[prices["symbol"].isin(tracked)].groupby("symbol")["date"].max() if tracked else \
            prices.groupby("symbol")["date"].max()
        limit = pd.Timestamp.now().normalize() - pd.Timedelta(days=5)
        stale = last[last < limit].index.tolist()
        if last.empty:
            add("Fraîcheur des cours", "warn", "aucun cours pour les sociétés suivies")
        else:
            add("Fraîcheur des cours", "ok" if not stale else "warn",
                f"dernière séance : {last.max():%Y-%m-%d}"
                + (f" — en retard : {', '.join(stale[:15])}" if stale else ""))

    # 4. Unicité des clés silver
    dup_tables = []
    for ds in DATASETS:
        if not ds.keys or not store.exists("silver", ds.name):
            continue
        df = store.read("silver", ds.name)
        keys = [k for k in ds.keys if k in df.columns]
        if keys and df.duplicated(subset=keys).any():
            dup_tables.append(ds.name)
    add("Unicité des clés (silver)", "ok" if not dup_tables else "error",
        "aucun doublon" if not dup_tables else f"doublons dans : {', '.join(dup_tables)}")

    # 5. Cohérence des états financiers (core) — signalée, jamais corrigée
    st = store.read("core", "financial_statements")
    if not st.empty and "quality_flags" in st.columns:
        exploded = st.assign(flag=st["quality_flags"].str.split(",")).explode("flag")
        exploded = exploded[exploded["flag"].fillna("") != ""]
        serious = exploded[~exploded["flag"].isin(["availability_estimated", "cost_of_revenue_zero"])]
        by_flag = serious.groupby("flag")["ticker"].agg(lambda t: sorted(set(t))).to_dict()
        add("Cohérence des états financiers", "ok" if not by_flag else "warn",
            "aucune anomalie" if not by_flag else
            " ; ".join(f"{k} : {', '.join(v[:8])}" for k, v in sorted(by_flag.items())))
        est = exploded[exploded["flag"] == "availability_estimated"]["ticker"].nunique()
        if est:
            add("Dates de publication", "warn", f"{est} société(s) sans date de dépôt : délai standard appliqué")

    # 6. Capitalisation approximée
    mc = store.read("core", "market_cap_daily")
    if not mc.empty:
        approx = mc[mc["source"] != "fmp_historical_market_cap"]["ticker"].unique().tolist()
        add("Capitalisation historique", "ok" if not approx else "warn",
            "source FMP" if not approx else f"approximée (cours × actions diluées) pour : {', '.join(approx)}")

    report = pd.DataFrame(rows)
    report["checked_at"] = pd.Timestamp.now()
    store.write("ops", "quality_report", report)
    for r in rows:
        log.log(logging.INFO if r["status"] == "ok" else logging.WARNING, "QA %-28s %-5s %s",
                r["check"], r["status"].upper(), r["detail"])
    return report
