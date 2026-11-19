"""Du flux de trésorerie d'exploitation au cash revenant à l'actionnaire (fonctions pures, testées).

Conventions (cf. docs/DECISIONS.md) : CAPEX, rachats, dividendes et acquisitions sont des montants positifs
dépensés ; l'émission nette de dette et la variation de trésorerie gardent leur signe publié.
La SBC n'est PAS une sortie de trésorerie : « FCF après SBC » est un indicateur analytique, hors cascade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .metrics import num

# (champ, libellé, transformation en flux signé de la cascade)
USES = [
    ("share_repurchases", "Rachats d'actions", lambda v: -abs(v)),
    ("dividends_paid", "Dividendes", lambda v: -abs(v)),
    ("acquisitions", "Acquisitions (nettes)", lambda v: -v),  # v > 0 = cash dépensé ; v < 0 = cessions nettes
    ("net_debt_issuance", "Dette : émission nette (+) / remboursement (−)", lambda v: v),
    ("stock_issuance", "Émissions d'actions", lambda v: abs(v)),
]


@dataclass
class Step:
    label: str
    value: float | None
    kind: str  # "relative" (variation), "total" (sous-total / total)
    note: str | None = None


def cash_bridge(row: dict[str, Any]) -> tuple[list[Step], list[str]]:
    """Cascade OCF -> CAPEX -> FCF -> utilisations -> variation de trésorerie.

    Retourne (étapes, postes indisponibles). Un poste indisponible n'est jamais mis à zéro : il est exclu de la
    cascade et absorbé, s'il y a lieu, dans « Autres flux / réconciliation », dont le libellé le mentionne.
    """
    ocf, capex = num(row.get("operating_cash_flow")), num(row.get("capex"))
    missing: list[str] = []
    if ocf is None:
        return [], ["Flux de trésorerie d'exploitation"]
    steps = [Step("Flux de trésorerie d'exploitation (OCF)", ocf, "relative")]
    if capex is None:
        missing.append("CAPEX")
        return steps, missing
    fcf = ocf - abs(capex)
    steps += [Step("CAPEX", -abs(capex), "relative"), Step("Free cash flow (FCF)", fcf, "total")]
    running = fcf
    for field, label, to_flow in USES:
        v = num(row.get(field))
        if v is None:
            missing.append(label)
            continue
        signed = to_flow(v)
        steps.append(Step(label, signed, "relative"))
        running += signed
    change = num(row.get("net_change_in_cash"))
    if change is None:
        missing.append("Variation de trésorerie")
        return steps, missing
    other = change - running
    note = "inclut les postes indisponibles : " + ", ".join(missing) if missing else None
    label = "Autres flux / réconciliation" + (" (dont postes indisponibles)" if missing else "")
    steps += [Step(label, other, "relative", note), Step("Variation nette de trésorerie", change, "total")]
    return steps, missing


def shareholder_metrics(row: dict[str, Any], market_cap: Any = None) -> dict[str, float | None]:
    fcf = None
    ocf, capex = num(row.get("operating_cash_flow")), num(row.get("capex"))
    if ocf is not None and capex is not None:
        fcf = ocf - abs(capex)
    sbc, rev = num(row.get("stock_based_compensation")), num(row.get("revenue"))
    buy, div = num(row.get("share_repurchases")), num(row.get("dividends_paid"))
    mc = num(market_cap)
    returned = (buy or 0) + (div or 0) if (buy is not None or div is not None) else None
    return {
        "fcf": fcf,
        "sbc": sbc,
        "sbc_to_revenue": sbc / rev if sbc is not None and rev and rev > 0 else None,
        "fcf_after_sbc": fcf - sbc if fcf is not None and sbc is not None else None,
        "buybacks": buy,
        "dividends": div,
        "returned": returned,
        "returned_to_fcf": returned / fcf if returned is not None and fcf and fcf > 0 else None,
        "dividend_yield": div / mc if div is not None and mc and mc > 0 else None,
        "buyback_yield": buy / mc if buy is not None and mc and mc > 0 else None,
    }


def buyback_effect(buybacks: Any, avg_price: Any, shares_start: Any, shares_end: Any) -> dict[str, float | None]:
    """Compare le nombre d'actions rachetées (estimé = montant / cours moyen de l'exercice) à la variation nette
    du nombre d'actions. L'écart approxime les émissions (rémunération en actions, conversions...).
    Estimation : le cours moyen n'est pas le prix réellement payé, et les actions moyennes pondérées diluées
    ne sont pas exactement le nombre d'actions en circulation."""
    b, p, s0, s1 = num(buybacks), num(avg_price), num(shares_start), num(shares_end)
    out: dict[str, float | None] = {"gross_bought_pct": None, "net_change_pct": None, "issued_pct": None}
    if s0 is None or s0 <= 0:
        return out
    if s1 is not None and s1 > 0:
        out["net_change_pct"] = s1 / s0 - 1
    if b is not None and p is not None and p > 0:
        out["gross_bought_pct"] = -(b / p) / s0
    if out["gross_bought_pct"] is not None and out["net_change_pct"] is not None:
        out["issued_pct"] = out["net_change_pct"] - out["gross_bought_pct"]
    return out
