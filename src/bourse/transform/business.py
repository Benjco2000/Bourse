"""gold.business_summaries : « Comment l'entreprise gagne de l'argent », résumé généré par IA.

Entrées : description sourcée (FMP) + répartition du CA par segment et par géographie (dernier exercice).
Le texte est explicitement marqué comme généré par IA dans l'application ; le modèle a pour consigne de
n'utiliser que ces entrées et de ne citer aucun chiffre absent des données fournies.
"""

from __future__ import annotations

import logging

import pandas as pd

from ..llm import LLM, LLMError, fingerprint
from ..storage import Store, utcnow

log = logging.getLogger(__name__)

SYSTEM = (
    "Tu es analyste financier. Tu expliques simplement, en français, comment une entreprise gagne de l'argent. "
    "Tu utilises UNIQUEMENT les informations fournies. Tu n'inventes aucun chiffre, client ou produit. "
    "Si une information manque, écris « non précisé dans les données sources »."
)

PROMPT = """Société : {name} ({ticker}) — secteur {sector}, industrie {industry}.

Description fournie par la source :
{description}

Répartition du chiffre d'affaires (exercice {fy}, en {currency}) :
{segments}

Rédige en Markdown, 120 à 200 mots, avec exactement ces 4 rubriques en gras :
**Produits et services**, **Clients**, **Modèle de revenus**, **Segments et géographies**.
Pour les segments, reprends les parts en % calculables à partir des chiffres fournis."""


def _segments_text(seg: pd.DataFrame) -> tuple[str, int | None]:
    if seg.empty:
        return "non disponible", None
    fy = int(seg["fiscal_year"].max()) if seg["fiscal_year"].notna().any() else None
    last = seg[seg["fiscal_year"] == fy] if fy else seg
    lines = []
    for kind, label in (("product", "Par activité"), ("geography", "Par zone géographique")):
        part = last[last["segment_type"] == kind]
        total = part["revenue"].sum()
        if part.empty or not total:
            continue
        items = ", ".join(f"{r.segment} {r.revenue / total:.0%}" for r in part.sort_values("revenue",
                                                                                         ascending=False).itertuples())
        lines.append(f"- {label} : {items}")
    return ("\n".join(lines) or "non disponible"), fy


def build_business_summaries(store: Store, llm: LLM | None) -> int:
    existing = store.read("gold", "business_summaries")
    if llm is None:
        log.info("Résumés IA : aucune clé ANTHROPIC_API_KEY, étape ignorée")
        return len(existing)
    companies = store.read("core", "companies")
    segments = store.read("core", "revenue_segments")
    snapshot = store.read("gold", "company_snapshot")
    cache = {r.ticker: r.input_hash for r in existing.itertuples()} if not existing.empty else {}
    rows = existing.to_dict("records") if not existing.empty else []
    generated = 0
    for c in companies.to_dict("records"):
        seg = segments[segments["ticker"] == c["ticker"]] if not segments.empty else pd.DataFrame()
        seg_txt, fy = _segments_text(seg)
        ccy = None
        if not snapshot.empty and c["ticker"] in set(snapshot["ticker"]):
            ccy = snapshot.loc[snapshot["ticker"] == c["ticker"], "reported_currency"].iloc[0]
        prompt = PROMPT.format(name=c.get("name"), ticker=c["ticker"], sector=c.get("sector"),
                               industry=c.get("industry"), description=c.get("description") or "non disponible",
                               fy=fy or "n.d.", currency=ccy or "devise des comptes", segments=seg_txt)
        h = fingerprint(llm.model, SYSTEM, prompt)
        if cache.get(c["ticker"]) == h:
            continue
        try:
            text = llm.complete(SYSTEM, prompt)
        except (LLMError, OSError) as exc:
            log.warning("Résumé IA impossible pour %s : %s", c["ticker"], exc)
            continue
        rows = [r for r in rows if r["ticker"] != c["ticker"]]
        rows.append({"ticker": c["ticker"], "summary_md": text.strip(), "model": llm.model, "input_hash": h,
                     "segments_fiscal_year": fy, "generated_at": utcnow().replace(tzinfo=None)})
        generated += 1
    if rows:
        store.write("gold", "business_summaries", pd.DataFrame(rows))
    log.info("Résumés IA : %d générés, %d en cache", generated, len(rows) - generated)
    return len(rows)
