import pandas as pd
import pytest

from bourse.transform.business import build_business_summaries
from bourse.transform.snapshot import snapshot_row

BUCKETS = {"small": 2e9, "mid": 10e9, "large": 100e9}


def _ttm(**kw):
    base = {"period_end": pd.Timestamp("2026-09-30"), "available_date": pd.Timestamp("2026-10-30"),
            "method": "sum_4_quarters", "reported_currency": "USD", "revenue": 100.0,
            "operating_cash_flow": 40.0, "free_cash_flow": 25.0}
    return pd.Series(base | kw)


def _mc(value=1000.0, source="fmp_historical_market_cap"):
    return pd.Series({"date": pd.Timestamp("2026-11-19"), "market_cap": value, "source": source})


EMPTY = pd.DataFrame(columns=["period_end", "quality_flags", "availability_estimated"])


def test_snapshot_multiples():
    r = snapshot_row({"ticker": "X", "trading_currency": "USD"}, _mc(), _ttm(), EMPTY, BUCKETS)
    assert r["ps"] == pytest.approx(10)
    assert r["pocf"] == pytest.approx(25)
    assert r["pfcf"] == pytest.approx(40)
    assert r["quality_level"] == "complete"


def test_snapshot_negative_fcf_and_annual_fallback():
    r = snapshot_row({"ticker": "X", "trading_currency": "USD"}, _mc(source="approx_close_x_diluted_shares"),
                     _ttm(free_cash_flow=-5.0, method="annual", fiscal_year=2025), EMPTY, BUCKETS)
    assert r["pfcf"] is None and "négatif" in r["pfcf_note"]
    assert "approximée" in r["quality_notes"] and "annuel" in r["quality_notes"]


def test_snapshot_currency_mismatch_blocks_multiples():
    r = snapshot_row({"ticker": "X", "trading_currency": "USD"}, _mc(), _ttm(reported_currency="EUR"), EMPTY,
                     BUCKETS)
    assert r["ps"] is None and r["ps_note"] == "devises différentes"


class FakeLLM:
    model = "fake-model"

    def __init__(self):
        self.calls = 0

    def complete(self, system, prompt, max_tokens=800):
        self.calls += 1
        assert "UNIQUEMENT" in system and "Répartition du chiffre d'affaires" in prompt
        return "**Produits et services** : ..."


def test_business_summaries_are_cached(demo_store):
    llm = FakeLLM()
    n = build_business_summaries(demo_store, llm)
    assert n == 4 and llm.calls == 4
    df = demo_store.read("gold", "business_summaries")
    assert set(df["model"]) == {"fake-model"}
    build_business_summaries(demo_store, llm)
    assert llm.calls == 4  # entrées inchangées -> aucun nouvel appel
    assert build_business_summaries(demo_store, None) == 4  # sans clé : on garde l'existant


def test_make_llm_provider_selection(monkeypatch):
    from bourse.llm import AnthropicLLM, OpenAILLM, make_llm

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert make_llm({"provider": "openai"}) is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm = make_llm({"provider": "openai", "model": "gpt-6-luna"})
    assert isinstance(llm, OpenAILLM) and llm.model == "gpt-6-luna"
    assert make_llm({"provider": "openai", "enabled": False}) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-test")
    assert isinstance(make_llm({"provider": "anthropic"}), AnthropicLLM)


def test_openai_responses_parsing(monkeypatch):
    from bourse import llm as llm_mod

    captured = {}

    class Resp:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"status": "completed", "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": "Bonjour"}]}]}

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, body=json)
        return Resp()

    monkeypatch.setattr(llm_mod.requests, "post", fake_post)
    out = llm_mod.OpenAILLM("sk-test", "gpt-6-luna").complete("sys", "prompt")
    assert out == "Bonjour"
    assert captured["url"].endswith("/v1/responses")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["body"]["instructions"] == "sys" and captured["body"]["input"] == "prompt"


def test_llm_disabled_after_exhausted_credit(monkeypatch):
    """Crédit épuisé : un seul appel HTTP, puis le client refuse sans rappeler l'API."""
    import pytest

    from bourse import llm as llm_mod

    calls = []

    class Resp:
        status_code = 429
        text = '{"error": {"code": "credit_balance_exhausted", "message": "no credits"}}'

    def fake_post(url, headers, json, timeout):
        calls.append(url)
        return Resp()

    monkeypatch.setattr(llm_mod.requests, "post", fake_post)
    client = llm_mod.OpenAILLM("sk-test", "gpt-6-luna")
    for _ in range(3):
        with pytest.raises(llm_mod.LLMUnavailableError):
            client.complete("sys", "prompt")
    assert len(calls) == 1


def test_business_summaries_stop_when_llm_unavailable(demo_store):
    from bourse.llm import LLMUnavailableError
    from bourse.transform.business import build_business_summaries

    class Broke:
        model = "x"
        calls = 0

        def complete(self, system, prompt, max_tokens=800):
            Broke.calls += 1
            raise LLMUnavailableError("credit_balance_exhausted")

    build_business_summaries(demo_store, Broke())
    assert Broke.calls == 1
