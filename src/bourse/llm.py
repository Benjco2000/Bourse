"""Clients LLM isolés (OpenAI Responses API ou Anthropic Messages API, via HTTP) pour les textes générés par IA.

Fournisseur choisi par `llm.provider` dans config/settings.yaml. Sans la clé correspondante
(`OPENAI_API_KEY` ou `ANTHROPIC_API_KEY`), aucune génération n'a lieu : l'application n'affiche alors que les
textes sourcés. Les générations sont faites dans le pipeline (jamais au chargement d'une page) et
mises en cache selon une empreinte des données d'entrée.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Protocol

import requests

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMUnavailableError(LLMError):
    """Erreur définitive pour le run (crédit épuisé, clé invalide) : inutile de réessayer."""


# Erreurs qui ne se résoudront pas pendant le run : le client se coupe après la première
FATAL_MARKERS = ("credit_balance_exhausted", "insufficient_quota", "invalid_api_key", "billing",
                 "credit balance is too low")


def _check_response(llm: _BaseLLM, status: int, text: str) -> None:
    if status < 400:
        return
    msg = f"HTTP {status} : {text[:200]}"
    if status in (401, 403) or any(m in text for m in FATAL_MARKERS):
        llm.disabled = msg
        log.warning("LLM désactivé pour la suite du run (%s) : les textes IA ne seront pas mis à jour, "
                    "le sentiment des actualités utilise le repli lexical. Détail : %s",
                    "clé refusée" if status in (401, 403) else "crédit épuisé", msg)
        raise LLMUnavailableError(msg)
    raise LLMError(msg)


class _BaseLLM:
    disabled: str | None = None

    def _guard(self) -> None:
        if self.disabled:
            raise LLMUnavailableError(self.disabled)


class LLM(Protocol):
    model: str

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> str: ...


class AnthropicLLM(_BaseLLM):
    url = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, timeout: float = 60):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> str:
        self._guard()
        resp = requests.post(
            self.url,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": self.model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=self.timeout,
        )
        _check_response(self, resp.status_code, resp.text)
        data = resp.json()
        return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")


class OpenAILLM(_BaseLLM):
    url = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str, model: str, timeout: float = 90):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> str:
        self._guard()
        resp = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            # marge pour les tokens de raisonnement, comptés dans max_output_tokens
            json={"model": self.model, "instructions": system, "input": prompt,
                  "max_output_tokens": max_tokens * 3},
            timeout=self.timeout,
        )
        _check_response(self, resp.status_code, resp.text)
        data = resp.json()
        if data.get("output_text"):
            return data["output_text"]
        texts = [c.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                 for c in item.get("content", []) if c.get("type") == "output_text"]
        if not texts:
            raise LLMError(f"réponse sans texte (statut {data.get('status')})")
        return "".join(texts)


DEFAULT_MODELS = {"openai": "gpt-6-luna", "anthropic": "claude-sonnet-5-5"}
KEY_VARS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def make_llm(settings_section: dict) -> LLM | None:
    if not settings_section.get("enabled", True):
        return None
    provider = str(settings_section.get("provider", "openai")).lower()
    key = os.getenv(KEY_VARS.get(provider, ""), "")
    if not key:
        return None
    model = settings_section.get("model") or DEFAULT_MODELS[provider]
    return OpenAILLM(key, model) if provider == "openai" else AnthropicLLM(key, model)


def fingerprint(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]
