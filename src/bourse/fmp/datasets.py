"""Catalogue déclaratif des jeux de données FMP ingérés.

Ajouter un jeu de données = ajouter une entrée ici (endpoint, paramètres, clés, fréquence).
Le reste du pipeline (bronze -> silver) est générique.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

Scope = Literal["symbol", "market"]
Mode = Literal["upsert", "replace"]
Frequency = Literal["daily", "weekly"]


@dataclass(frozen=True)
class Dataset:
    name: str                      # nom de la table silver
    path: str                      # endpoint relatif à /stable
    scope: Scope = "symbol"        # un appel par ticker, ou un appel global "marché"
    keys: tuple[str, ...] = ()     # clés d'unicité (colonnes snake_case) pour l'upsert silver
    frequency: Frequency = "daily"
    mode: Mode = "upsert"          # replace = la table silver = le dernier snapshot
    symbol_param: str = "symbol"
    # True = on ne re-télécharge que depuis la dernière date connue (paramètre `from`)
    incremental: bool = False
    # Paramètres dynamiques (dépendant de la config / de la date du jour)
    params: Callable[[dict[str, Any]], dict[str, Any]] = field(default=lambda cfg: {})
    description: str = ""


def _today() -> date:
    return date.today()


DATASETS: list[Dataset] = [
    # ----------------------------------------------------------------- par ticker
    Dataset("profile", "profile", keys=("symbol",), frequency="weekly",
            description="Profil société (secteur, description, logo, dirigeant...)"),
    Dataset("quote", "quote", keys=("symbol",),
            description="Dernière cotation (prix, variation, plus hauts/bas 52 semaines...)"),
    Dataset("prices_daily", "historical-price-eod/full", keys=("symbol", "date"), incremental=True,
            params=lambda cfg: {"from": cfg["history"].get("prices_start", "2015-01-01")},
            description="Historique des cours journaliers"),
    Dataset("income_statement_annual", "income-statement", keys=("symbol", "date", "period"),
            frequency="weekly",
            params=lambda cfg: {"period": "annual", "limit": cfg["history"].get("annual_statements_limit", 15)}),
    Dataset("income_statement_quarter", "income-statement", keys=("symbol", "date", "period"),
            frequency="weekly",
            params=lambda cfg: {"period": "quarter", "limit": cfg["history"].get("quarterly_statements_limit", 8)}),
    Dataset("balance_sheet_annual", "balance-sheet-statement", keys=("symbol", "date", "period"),
            frequency="weekly",
            params=lambda cfg: {"period": "annual", "limit": cfg["history"].get("annual_statements_limit", 15)}),
    Dataset("balance_sheet_quarter", "balance-sheet-statement", keys=("symbol", "date", "period"),
            frequency="weekly",
            params=lambda cfg: {"period": "quarter", "limit": cfg["history"].get("quarterly_statements_limit", 8)}),
    Dataset("cash_flow_annual", "cash-flow-statement", keys=("symbol", "date", "period"),
            frequency="weekly",
            params=lambda cfg: {"period": "annual", "limit": cfg["history"].get("annual_statements_limit", 15)}),
    Dataset("cash_flow_quarter", "cash-flow-statement", keys=("symbol", "date", "period"),
            frequency="weekly",
            params=lambda cfg: {"period": "quarter", "limit": cfg["history"].get("quarterly_statements_limit", 8)}),
    Dataset("ratios_annual", "ratios", keys=("symbol", "date", "period"), frequency="weekly",
            params=lambda cfg: {"period": "annual", "limit": cfg["history"].get("annual_statements_limit", 15)}),
    Dataset("key_metrics_annual", "key-metrics", keys=("symbol", "date", "period"), frequency="weekly",
            params=lambda cfg: {"period": "annual", "limit": cfg["history"].get("annual_statements_limit", 15)}),
    Dataset("financial_scores", "financial-scores", keys=("symbol",), frequency="weekly",
            description="Altman Z-Score, Piotroski F-Score"),
    Dataset("dividends", "dividends", keys=("symbol", "date"),
            description="Historique des dividendes"),
    Dataset("dcf_fmp", "discounted-cash-flow", keys=("symbol",),
            description="DCF calculé par FMP"),
    Dataset("price_target_consensus", "price-target-consensus", keys=("symbol",)),
    Dataset("grades_consensus", "grades-consensus", keys=("symbol",)),
    Dataset("analyst_estimates", "analyst-estimates", keys=("symbol", "date"), frequency="weekly",
            params=lambda cfg: {"period": "annual", "limit": 10}),
    Dataset("stock_news", "news/stock", keys=("url",), symbol_param="symbols",
            params=lambda cfg: {"limit": cfg["history"].get("news_limit", 30)}),
    Dataset("insider_trades", "insider-trading/search",
            keys=("symbol", "filing_date", "transaction_date", "reporting_name", "transaction_type",
                  "securities_transacted", "price"),
            params=lambda cfg: {"limit": 50}),
    Dataset("market_cap_history", "historical-market-capitalization", keys=("symbol", "date"), incremental=True,
            params=lambda cfg: {"from": cfg["history"].get("prices_start", "2015-01-01"), "limit": 5000},
            description="Capitalisation boursière quotidienne historique"),
    Dataset("stock_peers", "stock-peers", keys=("query_symbol", "symbol"), frequency="weekly",
            description="Concurrents proposés par FMP"),
    Dataset("revenue_segments_product", "revenue-product-segmentation", keys=("symbol", "date"),
            frequency="weekly", params=lambda cfg: {"period": "annual", "structure": "flat"}),
    Dataset("revenue_segments_geo", "revenue-geographic-segmentation", keys=("symbol", "date"),
            frequency="weekly", params=lambda cfg: {"period": "annual", "structure": "flat"}),
    # -------------------------------------------------------------------- marché
    Dataset("market_gainers", "biggest-gainers", scope="market", mode="replace"),
    Dataset("market_losers", "biggest-losers", scope="market", mode="replace"),
    Dataset("market_actives", "most-actives", scope="market", mode="replace"),
    Dataset("general_news", "news/general-latest", scope="market", keys=("url",),
            params=lambda cfg: {"page": 0, "limit": 50}),
    Dataset("earnings_calendar", "earnings-calendar", scope="market", keys=("symbol", "date"),
            params=lambda cfg: {"from": (_today() - timedelta(days=7)).isoformat(),
                                "to": (_today() + timedelta(days=60)).isoformat()}),
    Dataset("treasury_rates", "treasury-rates", scope="market", keys=("date",),
            params=lambda cfg: {"from": (_today() - timedelta(days=30)).isoformat(),
                                "to": _today().isoformat()}),
    Dataset("sp500_constituents", "sp500-constituent", scope="market", mode="replace",
            frequency="weekly"),
]

DATASETS_BY_NAME = {d.name: d for d in DATASETS}
