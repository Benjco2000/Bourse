"""Client HTTP pour l'API Financial Modeling Prep (endpoints `stable`).

- limiteur de débit thread-safe (token bucket) pour respecter le quota par minute ;
- retries avec backoff exponentiel sur 429 / 5xx / erreurs réseau ;
- détection des endpoints non inclus dans votre plan (401/402/403) -> `PlanRestrictedError`,
  pour que l'ingestion saute proprement ces jeux de données au lieu de planter.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

log = logging.getLogger(__name__)


class FMPError(RuntimeError):
    pass


class PlanRestrictedError(FMPError):
    """L'endpoint n'est pas accessible avec la clé/le plan actuel."""


class QuotaExceededError(FMPError):
    """Quota journalier atteint : inutile de réessayer avant demain."""


class RateLimiter:
    """Token bucket : `rate` requêtes par `per` secondes, partagé entre threads."""

    def __init__(self, rate: int, per: float = 60.0):
        self.capacity = max(1, rate)
        self.tokens = float(self.capacity)
        self.fill_rate = self.capacity / per
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.fill_rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.fill_rate
            time.sleep(wait)


class FMPClient:
    def __init__(self, api_key: str, base_url: str = "https://financialmodelingprep.com/stable",
                 requests_per_minute: int = 250, timeout: float = 30, max_retries: int = 4):
        if not api_key:
            raise FMPError(
                "FMP_API_KEY manquante : copiez .env.example en .env et renseignez votre clé "
                "(ou lancez `bourse demo` pour tester avec des données synthétiques)."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.limiter = RateLimiter(requests_per_minute)
        self.session = requests.Session()
        self.calls = 0
        self._calls_lock = threading.Lock()

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        query = {k: v for k, v in (params or {}).items() if v is not None}
        query["apikey"] = self.api_key

        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            with self._calls_lock:
                self.calls += 1
            try:
                resp = self.session.get(url, params=query, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise FMPError(f"{path}: erreur réseau {exc}") from exc
                self._sleep(attempt)
                continue

            if resp.status_code in (401, 402, 403):
                raise PlanRestrictedError(f"{path}: HTTP {resp.status_code} — {resp.text[:200]}")
            if resp.status_code == 429 and "limit reach" in resp.text.lower():
                raise QuotaExceededError(f"{path}: quota FMP atteint — {resp.text[:200]}")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self.max_retries:
                    raise FMPError(f"{path}: HTTP {resp.status_code} après {attempt + 1} tentatives")
                retry_after = resp.headers.get("Retry-After")
                self._sleep(attempt, float(retry_after) if retry_after and retry_after.isdigit() else None)
                continue
            if resp.status_code >= 400:
                raise FMPError(f"{path}: HTTP {resp.status_code} — {resp.text[:200]}")

            data = resp.json()
            # FMP renvoie parfois {"Error Message": "..."} avec un code 200
            if isinstance(data, dict) and ("Error Message" in data or "error" in data):
                message = data.get("Error Message") or data.get("error")
                if "limit reach" in str(message).lower():
                    raise QuotaExceededError(f"{path}: {message}")
                if "plan" in str(message).lower() or "subscription" in str(message).lower():
                    raise PlanRestrictedError(f"{path}: {message}")
                raise FMPError(f"{path}: {message}")
            return data
        raise FMPError(f"{path}: échec inattendu")

    @staticmethod
    def _sleep(attempt: int, seconds: float | None = None) -> None:
        delay = seconds if seconds is not None else min(60.0, 2 ** attempt)
        log.debug("Nouvel essai dans %.1fs", delay)
        time.sleep(delay)
