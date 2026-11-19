"""Faux client FMP générant des données SYNTHÉTIQUES déterministes (mêmes noms de champs que l'API).

Sert à : tester le pipeline sans clé/quota, faire tourner les tests, faire une démo de l'application.
Les chiffres n'ont AUCUNE valeur financière.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta
from typing import Any

import numpy as np

SECTORS = [("Technology", "Software"), ("Consumer Defensive", "Beverages"), ("Healthcare", "Drug Manufacturers"),
           ("Financial Services", "Banks"), ("Energy", "Oil & Gas"), ("Industrials", "Aerospace"),
           ("Communication Services", "Internet Content")]


def _rng(*parts: Any) -> np.random.Generator:
    seed = int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)
    return np.random.default_rng(seed)


class FakeFMPClient:
    def __init__(self, today: date | None = None):
        self.today = today or date.today()
        self.calls = 0

    # ------------------------------------------------------------------ profil "économique"
    def _company(self, sym: str) -> dict[str, Any]:
        r = _rng("company", sym)
        sector, industry = SECTORS[int(r.integers(len(SECTORS)))]
        revenue0 = float(r.uniform(3e9, 2e11))
        return {
            "sector": sector, "industry": industry,
            "revenue0": revenue0,
            "growth": float(r.uniform(-0.02, 0.14)),
            "margin": float(r.uniform(0.04, 0.35)),
            "shares": float(r.uniform(5e8, 8e9)),
            "pe": float(r.uniform(9, 38)),
            "equity_ratio": float(r.uniform(0.25, 0.6)),
            "payout": float(r.choice([0.0, 0.25, 0.4, 0.6])),
            "div_since": int(r.integers(1975, 2018)),
        }

    def _years(self, n: int) -> list[int]:
        last = self.today.year - 1
        return list(range(last - n + 1, last + 1))

    def _fundamentals(self, sym: str, year: int) -> dict[str, float]:
        c = self._company(sym)
        r = _rng("fund", sym, year)
        t = year - (self.today.year - 15)
        revenue = c["revenue0"] * (1 + c["growth"]) ** t * float(r.uniform(0.95, 1.05))
        margin = c["margin"] * float(r.uniform(0.8, 1.15))
        net = revenue * margin
        if r.random() < 0.04:
            net = -abs(net) * 0.3
        shares = c["shares"] * (1 - 0.01) ** t
        assets = revenue * float(r.uniform(1.0, 2.2))
        equity = assets * c["equity_ratio"]
        cur_assets = assets * 0.35
        cur_liab = cur_assets / float(r.uniform(0.9, 2.6))
        debt = assets * float(r.uniform(0.1, 0.35))
        ocf = net * float(r.uniform(1.05, 1.4))
        capex = -revenue * float(r.uniform(0.02, 0.1))
        return {
            "revenue": revenue, "net": net, "shares": shares, "assets": assets, "equity": equity,
            "cur_assets": cur_assets, "cur_liab": cur_liab, "debt": debt, "ocf": ocf, "capex": capex,
            "gross": revenue * min(0.85, margin + float(r.uniform(0.15, 0.4))),
            "opinc": net * 1.25, "tax": net * 0.2,
        }

    def _price(self, sym: str) -> float:
        c = self._company(sym)
        f = self._fundamentals(sym, self.today.year - 1)
        return round(max(5.0, abs(f["net"]) / f["shares"] * c["pe"]), 2)

    # ------------------------------------------------------------------------- API
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls += 1
        p = params or {}
        sym = str(p.get("symbol") or p.get("symbols") or "").upper()
        handler = {
            "profile": self._profile, "quote": self._quote, "historical-price-eod/full": self._prices,
            "income-statement": self._income, "balance-sheet-statement": self._balance,
            "cash-flow-statement": self._cashflow, "ratios": self._ratios, "key-metrics": self._key_metrics,
            "financial-scores": self._scores, "dividends": self._dividends, "discounted-cash-flow": self._dcf,
            "price-target-consensus": self._targets, "grades-consensus": self._grades,
            "analyst-estimates": self._estimates, "news/stock": self._news, "insider-trading/search": self._insiders,
            "biggest-gainers": lambda s, p: self._movers(1), "biggest-losers": lambda s, p: self._movers(-1),
            "most-actives": lambda s, p: self._movers(0), "news/general-latest": lambda s, p: self._general_news(),
            "earnings-calendar": lambda s, p: self._earnings(), "treasury-rates": lambda s, p: self._treasury(),
            "sp500-constituent": lambda s, p: [],
            "historical-market-capitalization": self._market_cap, "stock-peers": self._peers,
            "revenue-product-segmentation": lambda s, p: self._segments(s, p, "product"),
            "revenue-geographic-segmentation": lambda s, p: self._segments(s, p, "geo"),
        }.get(path)
        if handler is None:
            return []
        return handler(sym, p)

    def _profile(self, sym: str, p: dict) -> list[dict]:
        c = self._company(sym)
        price = self._price(sym)
        f = self._fundamentals(sym, self.today.year - 1)
        return [{
            "symbol": sym, "price": price, "marketCap": price * f["shares"], "beta": 1.05,
            "lastDividend": round(price * 0.01 * (c["payout"] > 0), 2), "range": f"{price*0.8:.2f}-{price*1.2:.2f}",
            "companyName": f"{sym} Demo Corp.", "currency": "USD", "exchange": "NASDAQ",
            "exchangeFullName": "NASDAQ Global Select", "industry": c["industry"], "sector": c["sector"],
            "country": "US", "ceo": "Jane Doe", "fullTimeEmployees": "120000", "website": "https://example.com",
            "description": f"{sym} Demo Corp. est une société fictive générée pour la démonstration de l'application. "
                           "Les données affichées sont synthétiques.",
            "image": "", "ipoDate": "1990-03-13", "isEtf": False, "isActivelyTrading": True,
        }]

    def _quote(self, sym: str, p: dict) -> list[dict]:
        prices = self._prices(sym, {"from": (self.today - timedelta(days=400)).isoformat()})
        closes = [x["close"] for x in prices]
        last, prev = prices[0], prices[1]
        f = self._fundamentals(sym, self.today.year - 1)
        return [{
            "symbol": sym, "name": f"{sym} Demo Corp.", "price": last["close"],
            "changePercentage": (last["close"] / prev["close"] - 1) * 100, "change": last["close"] - prev["close"],
            "volume": last["volume"], "dayLow": last["low"], "dayHigh": last["high"],
            "yearHigh": max(closes[:252]), "yearLow": min(closes[:252]),
            "marketCap": last["close"] * f["shares"], "priceAvg50": float(np.mean(closes[:50])),
            "priceAvg200": float(np.mean(closes[:200])), "exchange": "NASDAQ", "open": last["open"],
            "previousClose": prev["close"], "timestamp": 0,
        }]

    def _prices(self, sym: str, p: dict) -> list[dict]:
        start = date.fromisoformat(str(p.get("from", "2015-01-01")))
        start = max(start, date(2015, 1, 1))
        r = _rng("prices", sym)
        days = []
        d = date(2015, 1, 1)
        while d <= self.today:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        n = len(days)
        rets = r.normal(0.0003, 0.016, n)
        path = np.exp(np.cumsum(rets))
        path = path / path[-1] * self._price(sym)
        out = []
        for i, day in enumerate(days):
            if day < start:
                continue
            close = float(path[i])
            out.append({"symbol": sym, "date": day.isoformat(), "open": close * 0.998, "high": close * 1.01,
                        "low": close * 0.99, "close": round(close, 2), "volume": int(1e6 + 1e5 * (i % 17)),
                        "change": 0.0, "changePercent": 0.0, "vwap": close})
        return list(reversed(out))  # FMP renvoie du plus récent au plus ancien

    def _quarter_ends(self, n: int) -> list[date]:
        """n dernières fins de trimestre calendaire déjà publiées (> 45 jours), de la plus récente à la plus ancienne."""
        d = self.today - timedelta(days=45)
        q_month = ((d.month - 1) // 3) * 3 + 1
        end = date(d.year, q_month, 1) - timedelta(days=1)
        ends = []
        for _ in range(n):
            ends.append(end)
            end = date(end.year, ((end.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
        return ends

    def _statement(self, sym: str, p: dict, build) -> list[dict]:
        period = p.get("period", "annual")
        limit = int(p.get("limit", 10))
        rows = []
        if period == "annual":
            for y in reversed(self._years(limit)):
                end = date(y, 12, 31)
                row = build(y, self._fundamentals(sym, y), "FY", end, 1.0)
                filed = end + timedelta(days=60)
                rows.append(row | {"filingDate": filed.isoformat(), "acceptedDate": f"{filed.isoformat()} 16:05:00"})
        else:
            for end in self._quarter_ends(limit):
                noise = float(_rng("q", sym, end).uniform(0.9, 1.1))
                row = build(end.year, self._fundamentals(sym, end.year), f"Q{(end.month - 1) // 3 + 1}", end,
                            0.25 * noise)
                filed = end + timedelta(days=30)
                rows.append(row | {"filingDate": filed.isoformat(), "acceptedDate": f"{filed.isoformat()} 16:05:00"})
        return rows

    def _income(self, sym: str, p: dict) -> list[dict]:
        def build(y, f, period, d, k):
            return {"date": d.isoformat(), "symbol": sym, "reportedCurrency": "USD", "fiscalYear": str(y),
                    "period": period, "revenue": f["revenue"] * k, "costOfRevenue": (f["revenue"] - f["gross"]) * k,
                    "grossProfit": f["gross"] * k, "operatingIncome": f["opinc"] * k, "ebit": f["opinc"] * k,
                    "netIncome": f["net"] * k, "ebitda": f["opinc"] * 1.2 * k,
                    "incomeBeforeTax": f["net"] * 1.2 * k, "incomeTaxExpense": f["tax"] * k,
                    "interestExpense": f["debt"] * 0.04 * k, "eps": f["net"] * k / f["shares"],
                    "epsDiluted": f["net"] * k / f["shares"], "weightedAverageShsOut": f["shares"] * 0.99,
                    "weightedAverageShsOutDil": f["shares"]}
        return self._statement(sym, p, build)

    def _balance(self, sym: str, p: dict) -> list[dict]:
        def build(y, f, period, d, k):
            return {"date": d.isoformat(), "symbol": sym, "reportedCurrency": "USD", "fiscalYear": str(y),
                    "period": period, "totalAssets": f["assets"], "totalLiabilities": f["assets"] - f["equity"],
                    "totalStockholdersEquity": f["equity"], "totalEquity": f["equity"], "totalDebt": f["debt"],
                    "shortTermDebt": f["debt"] * 0.2, "longTermDebt": f["debt"] * 0.8,
                    "cashAndCashEquivalents": f["cur_assets"] * 0.3, "shortTermInvestments": f["cur_assets"] * 0.15,
                    "cashAndShortTermInvestments": f["cur_assets"] * 0.45,
                    "totalCurrentAssets": f["cur_assets"], "totalCurrentLiabilities": f["cur_liab"],
                    "totalLiabilitiesAndTotalEquity": f["assets"]}
        return self._statement(sym, p, build)

    def _cashflow(self, sym: str, p: dict) -> list[dict]:
        c = self._company(sym)

        def build(y, f, period, d, k):
            div = -max(f["net"], 0) * c["payout"] * k if y >= c["div_since"] else 0.0
            capex, ocf = f["capex"] * k, f["ocf"] * k
            buyback = -f["net"] * 0.2 * k
            acq = -f["revenue"] * 0.01 * k
            debt = f["debt"] * 0.02 * k
            change = ocf + capex + div + buyback + acq + debt
            return {"date": d.isoformat(), "symbol": sym, "reportedCurrency": "USD", "fiscalYear": str(y),
                    "period": period, "netIncome": f["net"] * k, "operatingCashFlow": ocf,
                    "netCashProvidedByOperatingActivities": ocf, "capitalExpenditure": capex,
                    "investmentsInPropertyPlantAndEquipment": capex, "freeCashFlow": ocf + capex,
                    "commonDividendsPaid": div, "netDividendsPaid": div, "commonStockRepurchased": buyback,
                    "commonStockIssuance": f["revenue"] * 0.001 * k, "stockBasedCompensation": f["revenue"] * 0.01 * k,
                    "acquisitionsNet": acq, "netDebtIssuance": debt, "netChangeInCash": change}
        return self._statement(sym, p, build)

    def _market_cap(self, sym: str, p: dict) -> list[dict]:
        f = self._fundamentals(sym, self.today.year - 1)
        return [{"symbol": sym, "date": x["date"], "marketCap": x["close"] * f["shares"]}
                for x in self._prices(sym, p)]

    def _peers(self, sym: str, p: dict) -> list[dict]:
        others = [s for s in ("MSFT", "AAPL", "GOOGL", "META", "KO", "PEP", "PG", "V", "MA", "AXP") if s != sym]
        return [{"symbol": s, "companyName": f"{s} Demo Corp.", "price": self._price(s), "mktCap": 1e11}
                for s in others[:5]]

    def _segments(self, sym: str, p: dict, kind: str) -> list[dict]:
        names = {"product": ["Produits", "Services", "Licences"], "geo": ["États-Unis", "Europe", "Asie-Pacifique"]}
        rows = []
        for y in reversed(self._years(5)):
            rev = self._fundamentals(sym, y)["revenue"]
            r = _rng("seg", sym, kind)
            w = r.dirichlet([3, 2, 1])
            rows.append({"symbol": sym, "fiscalYear": y, "period": "FY", "reportedCurrency": "USD",
                         "date": date(y, 12, 31).isoformat(),
                         "data": {n: float(rev * wi) for n, wi in zip(names[kind], w, strict=True)}})
        return rows

    def _ratios(self, sym: str, p: dict) -> list[dict]:
        c = self._company(sym)
        rows = []
        for y in reversed(self._years(int(p.get("limit", 10)))):
            r = _rng("ratio", sym, y)
            rows.append({"symbol": sym, "date": date(y, 12, 31).isoformat(), "fiscalYear": str(y), "period": "FY",
                         "priceToEarningsRatio": c["pe"] * float(r.uniform(0.75, 1.3))})
        return rows

    def _key_metrics(self, sym: str, p: dict) -> list[dict]:
        return [{"symbol": sym, "date": date(y, 12, 31).isoformat(), "fiscalYear": str(y), "period": "FY",
                 "marketCap": self._price(sym) * self._fundamentals(sym, y)["shares"]}
                for y in reversed(self._years(int(p.get("limit", 10))))]

    def _scores(self, sym: str, p: dict) -> list[dict]:
        r = _rng("scores", sym)
        return [{"symbol": sym, "reportedCurrency": "USD", "altmanZScore": float(r.uniform(1.2, 9)),
                 "piotroskiScore": int(r.integers(3, 10))}]

    def _dividends(self, sym: str, p: dict) -> list[dict]:
        c = self._company(sym)
        if c["payout"] == 0:
            return []
        rows = []
        amount = self._price(sym) * 0.006
        d = self.today - timedelta(days=30)
        while d.year >= c["div_since"]:
            rows.append({"symbol": sym, "date": d.isoformat(), "adjDividend": round(amount, 4),
                         "dividend": round(amount, 4), "frequency": "Quarterly"})
            d -= timedelta(days=91)
            amount /= 1.015
        return rows

    def _dcf(self, sym: str, p: dict) -> list[dict]:
        price = self._price(sym)
        return [{"symbol": sym, "date": self.today.isoformat(), "dcf": price * float(_rng("dcf", sym).uniform(0.7, 1.4)),
                 "Stock Price": price}]

    def _targets(self, sym: str, p: dict) -> list[dict]:
        price = self._price(sym)
        return [{"symbol": sym, "targetHigh": price * 1.4, "targetLow": price * 0.8,
                 "targetConsensus": price * 1.12, "targetMedian": price * 1.1}]

    def _grades(self, sym: str, p: dict) -> list[dict]:
        r = _rng("grades", sym)
        sb, b, h, s = (int(x) for x in r.integers(0, 25, 4))
        return [{"symbol": sym, "strongBuy": sb, "buy": b, "hold": h, "sell": s // 3, "strongSell": 0,
                 "consensus": "Buy" if sb + b > h else "Hold"}]

    def _estimates(self, sym: str, p: dict) -> list[dict]:
        f = self._fundamentals(sym, self.today.year - 1)
        c = self._company(sym)
        eps = abs(f["net"]) / f["shares"]
        return [{"symbol": sym, "date": date(self.today.year + i, 12, 31).isoformat(),
                 "revenueAvg": f["revenue"] * (1 + c["growth"]) ** (i + 1),
                 "epsAvg": eps * (1 + c["growth"] + 0.02) ** (i + 1), "numAnalystsEps": 20}
                for i in range(0, 4)]

    NEWS_TEMPLATES = [
        ("{s} beats expectations as revenue growth accelerates", 1),
        ("{s} raises full-year guidance after strong quarter", 1),
        ("{s} announces record share buyback program", 1),
        ("{s} shares fall after weak outlook and margin pressure", -1),
        ("Regulators open investigation into {s} practices", -1),
        ("{s} faces lawsuit over product recall", -1),
        ("{s} to present at industry conference next month", 0),
        ("{s} names new chief financial officer", 0),
    ]

    def _news(self, sym: str, p: dict) -> list[dict]:
        """~1 article tous les 3 jours sur 400 jours, dont des doublons (même titre, autre site)."""
        r = _rng("news", sym)
        rows = []
        for i in range(0, 400, 3):
            day = self.today - timedelta(days=i)
            title, _ = self.NEWS_TEMPLATES[int(r.integers(len(self.NEWS_TEMPLATES)))]
            base = {"symbol": sym, "publishedDate": f"{day.isoformat()} {8 + i % 10:02d}:00:00",
                    "publisher": "Demo Wire", "title": f"{title.format(s=sym)} (exemple)", "image": "",
                    "site": "example.com", "text": "Article fictif généré pour la démonstration.",
                    "url": f"https://example.com/{sym.lower()}/{i}"}
            rows.append(base)
            if i % 30 == 0:  # reprise du même article par un autre site
                rows.append(base | {"site": "mirror.example.org", "publisher": "Mirror",
                                    "url": f"https://mirror.example.org/{sym.lower()}/{i}",
                                    "publishedDate": f"{day.isoformat()} 23:00:00"})
        lo = str(p.get("from", "1900-01-01"))
        hi = str(p.get("to", "2999-12-31")) + " 99"
        rows = [x for x in rows if lo <= x["publishedDate"] <= hi]
        limit, page = int(p.get("limit", 100)), int(p.get("page", 0))
        return rows[page * limit:(page + 1) * limit]

    def _insiders(self, sym: str, p: dict) -> list[dict]:
        r = _rng("ins", sym)
        return [{"symbol": sym, "filingDate": (self.today - timedelta(days=10 * i)).isoformat(),
                 "transactionDate": (self.today - timedelta(days=10 * i + 2)).isoformat(),
                 "reportingName": ["DOE JANE", "SMITH JOHN", "MARTIN ALICE"][i % 3], "typeOfOwner": "officer",
                 "transactionType": "S-Sale" if r.random() < 0.7 else "P-Purchase",
                 "securitiesTransacted": int(r.integers(1000, 50000)), "price": self._price(sym) * 0.97,
                 "securitiesOwned": int(r.integers(1e5, 1e6))} for i in range(6)]

    def _movers(self, sign: int) -> list[dict]:
        r = _rng("movers", sign, self.today)
        return [{"symbol": f"DM{sign + 1}{i}", "name": f"Demo Mover {i}", "price": float(r.uniform(5, 200)),
                 "change": 1.0, "changesPercentage": float(sign * r.uniform(5, 30)) if sign else float(r.normal(0, 3)),
                 "exchange": "NYSE"} for i in range(10)]

    def _general_news(self) -> list[dict]:
        return [{"publishedDate": f"{(self.today - timedelta(hours=6 * i)).isoformat()} 09:00:00",
                 "publisher": "Demo Wire", "title": f"Actualité de marché fictive n°{i + 1}", "image": "",
                 "site": "example.com", "text": "Contenu fictif.", "url": f"https://example.com/market/{i}"}
                for i in range(12)]

    def _earnings(self) -> list[dict]:
        syms = ["MSFT", "AAPL", "KO", "JNJ", "NVDA", "PG", "XOM", "WMT"]
        return [{"symbol": s, "date": (self.today + timedelta(days=3 * i - 5)).isoformat(),
                 "epsEstimated": 1.2 + i / 10, "epsActual": (1.25 + i / 10) if i < 2 else None,
                 "revenueEstimated": 1e10 * (i + 1), "revenueActual": None} for i, s in enumerate(syms)]

    def _treasury(self) -> list[dict]:
        return [{"date": (self.today - timedelta(days=i)).isoformat(), "month3": 4.3, "year2": 4.0,
                 "year10": 4.2 + 0.01 * math.sin(i), "year30": 4.6} for i in range(10)]
