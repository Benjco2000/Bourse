import json
from datetime import date

import pandas as pd

from bourse.fmp.fake import FakeFMPClient
from bourse.ingestion import run_ingestion
from bourse.news import build_news, build_synthesis, lexicon_score, score_articles
from bourse.storage import Store


class FakeLLM:
    model = "fake"

    def __init__(self):
        self.calls = 0

    def complete(self, system, prompt, max_tokens=800):
        self.calls += 1
        if "tableau JSON" in system:
            n = len([line for line in prompt.splitlines() if line.strip()])
            return "Voici : " + json.dumps([{"id": i, "score": 0.5} for i in range(n)])
        return json.dumps({"themes": ["t"], "positives": ["p"], "negatives": ["n"], "risks": ["r"],
                           "sentiment": "positif", "confidence": "faible", "article_ids": [0, 1, 99]})


def test_lexicon_score():
    assert lexicon_score("Company beats estimates, record profit") > 0
    assert lexicon_score("Shares fall after lawsuit and weak outlook") < 0
    assert lexicon_score("Company to hold annual meeting") == 0


def test_news_pagination_and_initial_history(settings, store):
    seen = []

    class Spy(FakeFMPClient):
        def get(self, path, params=None):
            if path == "news/stock":
                seen.append(dict(params))
            return super().get(path, params)

    run_ingestion(settings, store, Spy(today=date(2026, 11, 19)), datasets=["stock_news"], tickers=["KO"],
                  force=True)
    assert [p["page"] for p in seen] == [0, 1]  # 2e page incomplète -> arrêt
    from datetime import timedelta

    assert seen[0]["from"] == (date.today() - timedelta(days=365)).isoformat()


def _news_store(tmp_path) -> Store:
    s = Store(tmp_path / "d")
    t0 = pd.Timestamp("2026-11-01")
    rows = [
        {"query_symbol": "KO", "published_date": t0, "title": "KO beats estimates", "site": "a", "url": "u1",
         "text": "", "_fetched_at": t0},
        {"query_symbol": "KO", "published_date": t0 + pd.Timedelta(hours=5), "title": "KO Beats Estimates!",
         "site": "b", "url": "u2", "text": "", "_fetched_at": t0},  # reprise
        {"query_symbol": "KO", "published_date": t0 + pd.Timedelta(days=20), "title": "KO beats estimates",
         "site": "a", "url": "u3", "text": "", "_fetched_at": t0},  # même titre, 20 jours plus tard : distinct
        {"query_symbol": "KO", "published_date": t0 + pd.Timedelta(days=21), "title": "Lawsuit hits KO",
         "site": "c", "url": "u4", "text": "", "_fetched_at": t0},
    ]
    s.write("silver", "stock_news", pd.DataFrame(rows))
    return s


def test_dedup_within_three_days(tmp_path):
    s = _news_store(tmp_path)
    news = build_news(s, pd.DataFrame({"ticker": ["KO"], "name": ["Coca-Cola"]}), ["KO"])
    prim = news[news["is_primary"]]
    assert set(prim["url"]) == {"u1", "u3", "u4"}
    assert int(news.loc[news["url"] == "u1", "duplicates"].iloc[0]) == 1


def test_scoring_cache_and_synthesis(tmp_path, settings):
    s = _news_store(tmp_path)
    build_news(s, pd.DataFrame({"ticker": ["KO"], "name": ["Coca-Cola"]}), ["KO"])
    # sans LLM : lexique
    sent = score_articles(s, None)
    assert set(sent["method"]) == {"lexicon"} and len(sent) == 3
    llm = FakeLLM()
    sent = score_articles(s, llm)
    assert (sent["method"] == "llm:fake").all() and llm.calls == 1
    score_articles(s, llm)
    assert llm.calls == 1  # déjà notés par LLM -> pas de nouvel appel
    synth = build_synthesis(settings, s, llm)
    row = synth.iloc[0]
    assert row["status"] == "ok" and row["score_class"] == "positif"
    parsed = json.loads(row["synthesis_json"])
    assert parsed["article_urls"] and len(parsed["article_urls"]) == 2  # id 99 ignoré
    calls = llm.calls
    build_synthesis(settings, s, llm)
    assert llm.calls == calls  # mêmes articles -> cache


def test_synthesis_insufficient(tmp_path, settings):
    s = _news_store(tmp_path)
    raw = s.read("silver", "stock_news").head(1)
    s.write("silver", "stock_news", raw)
    build_news(s, pd.DataFrame({"ticker": ["KO"], "name": ["Coca-Cola"]}), ["KO"])
    score_articles(s, None)
    llm = FakeLLM()
    synth = build_synthesis(settings, s, llm)
    assert synth.iloc[0]["status"] == "insufficient" and llm.calls == 0
