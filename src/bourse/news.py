"""Actualités & sentiment : normalisation, déduplication, score par article, synthèse IA.

Tables :
    core.news             articles normalisés et dédupliqués (un article = une URL ; les reprises d'un même
                          titre sont regroupées dans `dup_group`, seul le premier publié est `is_primary`)
    gold.news_sentiment   score par article dans [-1 ; 1] : LLM si disponible, sinon lexique (méthode tracée)
    gold.news_synthesis   synthèse IA par société (thèmes, positifs, négatifs, risques, sentiment, confiance,
                          articles justificatifs), sur une fenêtre récente, mise en cache
Le sentiment médiatique n'est pas une recommandation d'investissement.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import pandas as pd

from .config import Settings
from .llm import LLM, LLMError, LLMUnavailableError, fingerprint
from .storage import Store, utcnow

log = logging.getLogger(__name__)

# Lexique financier minimal (anglais, inspiré des catégories de Loughran-McDonald) — repli sans LLM.
POSITIVE = {"beat", "beats", "record", "growth", "raise", "raises", "raised", "upgrade", "upgraded", "strong",
            "surge", "surges", "gain", "gains", "outperform", "profit", "profitable", "accelerates", "expands",
            "buyback", "dividend", "approval", "approved", "win", "wins", "rally", "boost", "boosts", "exceeds",
            "tops", "higher", "rebound", "breakthrough", "partnership"}
NEGATIVE = {"miss", "misses", "missed", "fall", "falls", "fell", "drop", "drops", "decline", "declines", "weak",
            "downgrade", "downgraded", "lawsuit", "investigation", "probe", "recall", "loss", "losses", "cut",
            "cuts", "layoffs", "warning", "warns", "slump", "plunge", "plunges", "fine", "fined", "pressure",
            "lower", "risk", "risks", "concern", "concerns", "delay", "delays", "antitrust", "breach", "fraud"}


def _norm_title(title: str) -> str:
    t = re.sub(r"[^a-z0-9 ]", " ", str(title).lower())
    return " ".join(t.split())[:90]


def build_news(store: Store, companies: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    raw = store.read("silver", "stock_news")
    cols = ["ticker", "published_at", "title", "source", "url", "summary", "relevance", "dup_group",
            "duplicates", "is_primary", "retrieved_at"]
    if raw.empty or "query_symbol" not in raw.columns:
        df = pd.DataFrame(columns=cols)
        store.write("core", "news", df)
        return df
    df = pd.DataFrame({
        "ticker": raw["query_symbol"], "published_at": pd.to_datetime(raw.get("published_date")),
        "title": raw.get("title"), "source": raw.get("site", raw.get("publisher")), "url": raw["url"],
        "summary": raw.get("text"), "retrieved_at": raw.get("_fetched_at"),
    })
    if tickers:
        df = df[df["ticker"].isin(tickers)]
    df = df.dropna(subset=["url", "title"]).drop_duplicates(["ticker", "url"])
    names = dict(zip(companies.get("ticker", []), companies.get("name", []), strict=False))

    def relevance(r: Any) -> float:
        name = str(names.get(r.ticker) or "").split(" ")[0].lower()
        title = str(r.title).lower()
        return 1.0 if r.ticker.lower() in title.split() or (name and name in title) else 0.6

    df["relevance"] = [relevance(r) for r in df.itertuples()]
    # Doublons = même titre normalisé, publiés à moins de 3 jours d'intervalle (reprises, syndication)
    df = df.sort_values("published_at")
    key = df["ticker"] + "|" + df["title"].map(_norm_title)
    cluster = []
    last_start: dict[str, tuple[pd.Timestamp, int]] = {}
    counter = 0
    for k, ts in zip(key, df["published_at"], strict=True):
        start = last_start.get(k)
        if start is None or pd.isna(ts) or (ts - start[0]) > pd.Timedelta(days=3):
            counter += 1
            last_start[k] = (ts, counter)
        cluster.append(f"{k}|{last_start[k][1]}")
    df["dup_group"] = cluster
    df["duplicates"] = df.groupby("dup_group")["url"].transform("count") - 1
    df["is_primary"] = ~df.duplicated("dup_group", keep="first")
    df = df[cols].sort_values(["ticker", "published_at"], ascending=[True, False]).reset_index(drop=True)
    store.write("core", "news", df)
    log.info("core.news : %d articles (%d après déduplication)", len(df), int(df["is_primary"].sum()))
    return df


def lexicon_score(text: str) -> float:
    words = re.findall(r"[a-z]+", str(text).lower())
    pos = sum(w in POSITIVE for w in words)
    neg = sum(w in NEGATIVE for w in words)
    return 0.0 if pos + neg == 0 else (pos - neg) / (pos + neg)


def _extract_json(text: str) -> Any:
    m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
    if not m:
        raise ValueError("aucun JSON dans la réponse")
    return json.loads(m.group(1))


SCORE_SYSTEM = ("Tu évalues le ton d'articles financiers pour l'entreprise indiquée. Pour chaque article, donne un "
                "score de -1 (très négatif pour l'entreprise) à 1 (très positif), 0 si neutre ou purement factuel. "
                "Réponds UNIQUEMENT avec un tableau JSON [{\"id\": <id>, \"score\": <nombre>}].")


def score_articles(store: Store, llm: LLM | None, max_llm: int = 400, batch: int = 25) -> pd.DataFrame:
    news = store.read("core", "news")
    existing = store.read("gold", "news_sentiment")
    if news.empty:
        return existing
    news = news[news["is_primary"]]
    done = existing[existing["method"].str.startswith("llm")] if not existing.empty else existing
    llm_done = set(done["url"]) if not done.empty else set()
    rows = existing[existing["url"].isin(llm_done)].to_dict("records") if not existing.empty else []
    todo = news[~news["url"].isin(llm_done)].sort_values("published_at", ascending=False)
    llm_scores: dict[str, float] = {}
    if llm is not None and not todo.empty:
        candidates = todo.head(max_llm)
        for start in range(0, len(candidates), batch):
            chunk = candidates.iloc[start:start + batch]
            lines = [f"{i}. [{r.ticker}] {r.title} — {str(r.summary or '')[:200]}" for i, r in
                     enumerate(chunk.itertuples())]
            try:
                parsed = _extract_json(llm.complete(SCORE_SYSTEM, "\n".join(lines), max_tokens=1200))
                for item in parsed:
                    i, sc = int(item["id"]), float(item["score"])
                    if 0 <= i < len(chunk):
                        llm_scores[chunk.iloc[i]["url"]] = max(-1.0, min(1.0, sc))
            except LLMUnavailableError:
                break  # déjà signalé : repli lexical pour le reste
            except (LLMError, ValueError, KeyError, TypeError, OSError) as exc:
                log.warning("Notation LLM d'un lot d'articles impossible (%s) : repli lexical", exc)
    now = utcnow().replace(tzinfo=None)
    model = f"llm:{llm.model}" if llm is not None else None
    for r in todo.itertuples():
        if r.url in llm_scores:
            rows.append({"url": r.url, "ticker": r.ticker, "score": llm_scores[r.url], "method": model,
                         "scored_at": now})
        else:
            rows.append({"url": r.url, "ticker": r.ticker, "score": lexicon_score(f"{r.title} {r.summary or ''}"),
                         "method": "lexicon", "scored_at": now})
    out = pd.DataFrame(rows)
    store.write("gold", "news_sentiment", out)
    log.info("gold.news_sentiment : %d articles (%d notés par LLM)", len(out),
             int(out["method"].str.startswith("llm").sum()))
    return out


SYNTH_SYSTEM = (
    "Tu es analyste financier. À partir UNIQUEMENT des articles fournis, tu produis une synthèse en français. "
    "Tu n'inventes rien et tu ne donnes aucune recommandation d'achat ou de vente. Réponds UNIQUEMENT en JSON : "
    '{"themes": [..], "positives": [..], "negatives": [..], "risks": [..], '
    '"sentiment": "positif"|"neutre"|"négatif", "confidence": "faible"|"moyenne"|"élevée", '
    '"article_ids": [ids des articles qui justifient la synthèse]}. 2 à 4 éléments courts par liste.'
)


def classify(mean_score: float | None, threshold: float) -> str | None:
    if mean_score is None or pd.isna(mean_score):
        return None
    return "positif" if mean_score > threshold else "négatif" if mean_score < -threshold else "neutre"


def build_synthesis(settings: Settings, store: Store, llm: LLM | None) -> pd.DataFrame:
    cfg = settings.section("news")
    window = int(cfg.get("synthesis_window_days", 30))
    max_n = int(cfg.get("synthesis_max_articles", 40))
    min_n = int(cfg.get("min_articles_for_synthesis", 3))
    thr = float(cfg.get("positive_threshold", 0.15))
    news = store.read("core", "news")
    sent = store.read("gold", "news_sentiment")
    existing = store.read("gold", "news_synthesis")
    # seules les synthèses abouties (ou sans assez d'articles) sont en cache : un échec est retenté au run suivant
    cache = {r.ticker: r.input_hash for r in existing.itertuples()
             if getattr(r, "status", "ok") in ("ok", "insufficient")} if not existing.empty else {}
    rows = existing.to_dict("records") if not existing.empty else []
    if news.empty:
        return existing
    news = news[news["is_primary"]].merge(sent[["url", "score", "method"]], on="url", how="left") \
        if not sent.empty else news[news["is_primary"]].assign(score=float("nan"), method=None)
    for ticker, g in news.groupby("ticker"):
        end = g["published_at"].max()
        recent = g[g["published_at"] >= end - pd.Timedelta(days=window)]
        recent = recent.sort_values(["relevance", "published_at"], ascending=False).head(max_n)
        weights = recent["relevance"]
        mean = float((recent["score"] * weights).sum() / weights.sum()) if len(recent) and \
            recent["score"].notna().any() else None
        base = {"ticker": ticker, "window_start": end - pd.Timedelta(days=window), "window_end": end,
                "n_articles": len(recent), "mean_score": mean, "score_class": classify(mean, thr),
                "urls": json.dumps(recent["url"].tolist())}
        h = fingerprint(llm.model if llm else None, SYNTH_SYSTEM, sorted(recent["url"]))
        if cache.get(ticker) == h:
            continue
        rec = base | {"input_hash": h, "synthesis_json": None, "model": None,
                      "generated_at": utcnow().replace(tzinfo=None),
                      "status": "insufficient" if len(recent) < min_n else "no_llm"}
        if llm is not None and len(recent) >= min_n:
            lines = [f"{i}. {r.published_at:%Y-%m-%d} — {r.title} ({r.source}) — {str(r.summary or '')[:300]}"
                     for i, r in enumerate(recent.itertuples())]
            try:
                parsed = _extract_json(llm.complete(SYNTH_SYSTEM, f"Entreprise : {ticker}\n\n" + "\n".join(lines),
                                                    max_tokens=1500))
                ids = [int(i) for i in parsed.get("article_ids", []) if 0 <= int(i) < len(recent)]
                parsed["article_urls"] = [recent.iloc[i]["url"] for i in ids]
                rec.update(synthesis_json=json.dumps(parsed, ensure_ascii=False), model=llm.model, status="ok")
            except LLMUnavailableError:
                rec["status"] = "no_llm"
            except (LLMError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
                log.warning("Synthèse IA impossible pour %s : %s", ticker, exc)
                rec["status"] = "error"
        rows = [r for r in rows if r["ticker"] != ticker] + [rec]
    out = pd.DataFrame(rows)
    store.write("gold", "news_synthesis", out)
    return out


def build_news_layer(settings: Settings, store: Store, tickers: list[str], llm: LLM | None) -> dict[str, int]:
    companies = store.read("core", "companies")
    news = build_news(store, companies, tickers)
    sent = score_articles(store, llm, int(settings.section("llm").get("max_articles_scored_per_run", 400)))
    synth = build_synthesis(settings, store, llm)
    return {"news": len(news), "news_sentiment": len(sent), "news_synthesis": len(synth)}
