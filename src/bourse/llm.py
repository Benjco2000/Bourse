"""Client LLM isolé (API Anthropic Messages, via HTTP) — utilisé pour les textes générés par IA.

Sans clé `ANTHROPIC_API_KEY`, aucune génération n'a lieu : l'application n'affiche alors que les
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


class LLM(Protocol):
    model: str

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> str: ...


class AnthropicLLM:
    url = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, timeout: float = 60):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, prompt: str, max_tokens: int = 800) -> str:
        resp = requests.post(
            self.url,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": self.model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise LLMError(f"HTTP {resp.status_code} : {resp.text[:200]}")
        data = resp.json()
        return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")


def make_llm(settings_section: dict) -> LLM | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key or not settings_section.get("enabled", True):
        return None
    return AnthropicLLM(key, settings_section.get("model", "claude-sonnet-5-5"))


def fingerprint(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]
