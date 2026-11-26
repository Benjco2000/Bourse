"""Entraînement, évaluation et enregistrement du modèle P/FCF.

Protocole (cf. docs/DECISIONS.md, section ML) :
- cible : log(P/FCF) ; métriques rapportées sur le multiple (après exponentielle) ;
- split chronologique train / validation / test avec embargo entre les blocs (les KPIs 5 ans se recouvrent),
  + un groupe de sociétés tirées au hasard, exclues de l'entraînement, évaluées sur la période de test ;
- sélection sur la validation uniquement ; un modèle plus simple à moins de `simplicity_tolerance` du meilleur
  est préféré ; le test n'est utilisé qu'une fois, pour le modèle retenu et la baseline ;
- intervalle de prédiction : quantiles 10 % / 90 % des résidus de validation (en log), couverture mesurée sur le test.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT, Settings
from ..storage import Store, utcnow
from . import registry
from .dataset import CAT_FEATURES, KPI_FEATURES
from .models import GroupMedianBaseline, PerSectorModel, complexity, make_candidates

log = logging.getLogger(__name__)
FEATURES = KPI_FEATURES + CAT_FEATURES


def metrics(y_log: np.ndarray, pred_log: np.ndarray) -> dict[str, float]:
    y, p = np.exp(y_log), np.exp(pred_log)
    err = np.abs(p - y)
    return {"mae": float(err.mean()), "medae": float(np.median(err)), "rmse": float(np.sqrt(((p - y) ** 2).mean())),
            "median_rel_error": float(np.median(err / y)), "mae_log": float(np.abs(pred_log - y_log).mean()),
            "n": int(len(y))}


def split(df: pd.DataFrame, cfg: dict[str, Any], seed: int = 42) -> dict[str, pd.Series]:
    """Masques booléens train / val / test (sociétés vues) / test_unseen (sociétés jamais vues)."""
    rng = np.random.default_rng(seed)
    tickers = np.array(sorted(df["ticker"].unique()))
    n_unseen = int(round(len(tickers) * float(cfg.get("unseen_companies_share", 0.2))))
    unseen = set(rng.choice(tickers, size=n_unseen, replace=False)) if n_unseen else set()
    is_unseen = df["ticker"].isin(unseen)
    d = df["date"]
    train = (d <= pd.Timestamp(cfg.get("train_end", "2020-12-31"))) & ~is_unseen
    val = (d >= pd.Timestamp(cfg.get("val_start", "2021-07-01"))) & \
        (d <= pd.Timestamp(cfg.get("val_end", "2022-12-31"))) & ~is_unseen
    test_period = d >= pd.Timestamp(cfg.get("test_start", "2023-07-01"))
    masks = {"train": train, "val": val, "test": test_period & ~is_unseen, "test_unseen": test_period & is_unseen}
    if min(masks["train"].sum(), masks["val"].sum(), masks["test"].sum()) < 30:
        # Historique trop court pour les dates configurées : découpage par quantiles de dates, avec embargo
        dates = np.sort(d.unique())
        q = lambda f: pd.Timestamp(dates[min(len(dates) - 1, int(f * len(dates)))])  # noqa: E731
        t_end, v_start, v_end, te_start = q(0.55), q(0.62), q(0.78), q(0.85)
        log.warning("Dates de split configurées inadaptées à l'historique : découpage par quantiles "
                    "(train ≤ %s, val %s–%s, test ≥ %s)", t_end.date(), v_start.date(), v_end.date(),
                    te_start.date())
        masks = {"train": (d <= t_end) & ~is_unseen, "val": (d >= v_start) & (d <= v_end) & ~is_unseen,
                 "test": (d >= te_start) & ~is_unseen, "test_unseen": (d >= te_start) & is_unseen}
    return masks


def _choose(val_scores: dict[str, dict[str, float]], tolerance: float) -> str:
    best = min(val_scores, key=lambda k: val_scores[k]["medae"])
    limit = val_scores[best]["medae"] * (1 + tolerance)
    eligible = [k for k, v in val_scores.items() if v["medae"] <= limit]
    return min(eligible, key=lambda k: (complexity(k), val_scores[k]["medae"]))


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def train(settings: Settings, store: Store, promote: bool | None = None) -> dict[str, Any]:
    import mlflow
    from mlflow.tracking import MlflowClient
    from sklearn.inspection import permutation_importance

    ml = settings.section("ml")
    df = store.read("gold", "ml_dataset")
    card = store.read_json("gold", "ml_dataset_card", default={}) or {}
    if len(df) < 200:
        raise ValueError(f"Dataset trop petit ({len(df)} observations) : lancez `bourse ml ingest` puis "
                         "`bourse ml dataset`.")
    df = df.sort_values(["date", "ticker"]).reset_index(drop=True)
    masks = split(df, ml.get("split", {}))
    X, y = df[FEATURES], df["target"].to_numpy()
    tr, va, te, teu = (masks[k].to_numpy() for k in ("train", "val", "test", "test_unseen"))

    # ---------------- candidats, évalués sur la validation
    candidates: dict[str, Any] = {"baseline": GroupMedianBaseline()}
    constraints = ml.get("monotonic_constraints") or {}
    candidates.update(make_candidates(with_segments=False, constraints=constraints))
    candidates.update(make_candidates(with_segments=True, constraints=constraints))
    min_sector = int(ml.get("min_sector_obs", 1500))
    big_sectors = [s for s, n in df.loc[tr, "sector"].value_counts().items() if n >= min_sector]
    if big_sectors:
        candidates["per_sector:hist_gradient_boosting"] = PerSectorModel(min_obs=min_sector, constraints=constraints)
    val_scores: dict[str, dict[str, float]] = {}
    fitted: dict[str, Any] = {}
    for name, model in candidates.items():
        model.fit(X[tr], y[tr])
        fitted[name] = model
        val_scores[name] = metrics(y[va], model.predict(X[va]))
        log.info("  %-38s val MedAE %.2f x | erreur relative médiane %.1f %%", name, val_scores[name]["medae"],
                 100 * val_scores[name]["median_rel_error"])
    selected = _choose(val_scores, float(ml.get("simplicity_tolerance", 0.02)))
    log.info("Modèle retenu (validation) : %s", selected)

    # ---------------- intervalle (résidus de validation du modèle entraîné sur train)
    resid = y[va] - fitted[selected].predict(X[va])
    q_lo, q_hi = (float(np.quantile(resid, 0.10)), float(np.quantile(resid, 0.90))) if len(resid) >= 20 \
        else (math.log(0.7), math.log(1.3))

    # ---------------- modèle final : réentraîné sur train + val ; test évalué une seule fois
    trva = tr | va
    final = _fresh(selected, min_sector, constraints)
    final.fit(X[trva], y[trva])
    baseline_final = GroupMedianBaseline().fit(X[trva], y[trva])
    test_m = metrics(y[te], final.predict(X[te])) if te.any() else {}
    test_base = metrics(y[te], baseline_final.predict(X[te])) if te.any() else {}
    unseen_m = metrics(y[teu], final.predict(X[teu])) if teu.any() else {}
    pred_te = final.predict(X[te]) if te.any() else np.array([])
    coverage = float(np.mean((y[te] >= pred_te + q_lo) & (y[te] <= pred_te + q_hi))) if te.any() else None

    # ---------------- importance des variables (permutation, sur le test)
    importance = pd.DataFrame(columns=["feature", "importance", "std"])
    if te.sum() >= 50:
        idx = np.flatnonzero(te)[:3000]
        pi = permutation_importance(final, X.iloc[idx], y[idx], scoring="neg_mean_absolute_error",
                                    n_repeats=5, random_state=42)
        importance = pd.DataFrame({"feature": FEATURES, "importance": pi.importances_mean,
                                   "std": pi.importances_std}).sort_values("importance", ascending=False)

    # ---------------- domaine couvert et verdict d'exploitabilité
    ref = df.loc[trva]
    domain = {f: {"p01": float(ref[f].quantile(0.01)), "p99": float(ref[f].quantile(0.99)),
                  "median": float(ref[f].median())} for f in KPI_FEATURES if ref[f].notna().any()}
    from .models import DEFAULT_MONOTONIC

    mono = DEFAULT_MONOTONIC | constraints
    violations = check_monotonicity(final, X[te] if te.any() else X[trva], mono, domain)
    bad = {f: v for f, v in violations.items() if v > 0.005}
    if bad:
        log.warning("Règles économiques non respectées : %s", bad)
    usab_cfg = ml.get("usability", {})
    reasons = []
    if bad:
        reasons.append("règles économiques non respectées : " + ", ".join(f"{f} ({v:.0%})" for f, v in bad.items()))
    if tr.sum() < int(usab_cfg.get("min_train_obs", 5000)):
        reasons.append(f"{int(tr.sum())} observations d'entraînement (< {usab_cfg.get('min_train_obs', 5000)})")
    n_comp = int(df.loc[trva, "ticker"].nunique())
    if n_comp < int(usab_cfg.get("min_companies", 300)):
        reasons.append(f"{n_comp} sociétés (< {usab_cfg.get('min_companies', 300)})")
    if usab_cfg.get("must_beat_baseline", True) and test_m and test_m["medae"] >= test_base["medae"]:
        reasons.append("ne fait pas mieux que la baseline (médiane par secteur et taille) sur le test")
    usable = not reasons

    meta = {
        "selected_model": selected, "trained_at": utcnow().isoformat(), "git_sha": _git_sha(),
        "features": FEATURES, "kpi_features": KPI_FEATURES, "target": "log(P/FCF)",
        "interval_log_quantiles": [q_lo, q_hi], "interval_level": 0.8,
        "n_train": int(tr.sum()), "n_val": int(va.sum()), "n_test": int(te.sum()), "n_test_unseen": int(teu.sum()),
        "n_companies": n_comp, "period": [str(df["date"].min().date()), str(df["date"].max().date())],
        "split": {k: [str(df.loc[m, "date"].min().date()), str(df.loc[m, "date"].max().date())]
                  if m.any() else None for k, m in masks.items()},
        "sectors": df.loc[trva, "sector"].value_counts().to_dict(),
        "cap_buckets": df.loc[trva, "cap_bucket"].value_counts().to_dict(),
        "domain": domain, "val_scores": val_scores, "test": test_m, "test_baseline": test_base,
        "test_unseen": unseen_m, "test_interval_coverage": coverage,
        "importance": importance.to_dict("records"),
        "monotonic_constraints": mono, "monotonicity_violations": violations,
        "usability": {"usable": usable, "reasons": reasons}, "dataset_card": card,
    }

    # ---------------- MLflow : suivi + registre
    registry.configure(settings)
    exp_id = registry.experiment(settings)
    name = registry.model_name(settings)
    with mlflow.start_run(experiment_id=exp_id, run_name=f"train_{selected}") as run:
        mlflow.log_params({"selected_model": selected, "n_train": meta["n_train"], "n_val": meta["n_val"],
                           "n_test": meta["n_test"], "n_companies": n_comp, "git_sha": meta["git_sha"],
                           "period": " → ".join(meta["period"]), "features": ",".join(FEATURES),
                           "split": json.dumps(meta["split"])})
        for cand, sc in val_scores.items():
            mlflow.log_metric(f"val_medae__{cand.replace(':', '_').replace('+', '_seg')}", sc["medae"])
        for prefix, m in (("test", test_m), ("test_baseline", test_base), ("test_unseen", unseen_m)):
            for k, v in m.items():
                mlflow.log_metric(f"{prefix}_{k}", v)
        if coverage is not None:
            mlflow.log_metric("test_interval_coverage_80", coverage)
        for f, v in violations.items():
            mlflow.log_metric(f"monotonic_violation__{f}", v)
        mlflow.log_param("monotonic_constraints", json.dumps(mono))
        mlflow.set_tags({"usable": str(usable), "usability_reasons": "; ".join(reasons) or "ok"})
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / registry.META_ARTIFACT).write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
            pd.DataFrame(val_scores).T.to_csv(t / "model_comparison_val.csv")
            importance.to_csv(t / "permutation_importance.csv", index=False)
            if te.any():
                pd.DataFrame({"ticker": df.loc[te, "ticker"], "date": df.loc[te, "date"],
                              "pfcf": np.exp(y[te]), "pred": np.exp(pred_te)}).to_csv(t / "test_predictions.csv",
                                                                                     index=False)
            mlflow.log_artifacts(tmp)
        # cloudpickle : modèles produits par ce pipeline uniquement (pas de fichiers tiers chargés)
        import warnings

        warnings.filterwarnings("ignore", message="Saving scikit-learn models in the pickle")
        info = mlflow.sklearn.log_model(final, name="model", input_example=X[trva].head(5),
                                        serialization_format="cloudpickle")
        mv = mlflow.register_model(info.model_uri, name)
        client = MlflowClient()
        client.set_model_version_tag(name, mv.version, "usable", str(usable))
        current = None
        try:
            current = client.get_model_version_by_alias(name, registry.CHAMPION)
        except Exception:
            pass
        should_promote = promote if promote is not None else _should_promote(client, current, test_m, usable)
        if should_promote:
            client.set_registered_model_alias(name, registry.CHAMPION, mv.version)
        log.info("Modèle %s version %s enregistré%s — test MedAE %.2f x (baseline %.2f x) — %s", name, mv.version,
                 " et désigné champion" if should_promote else "", test_m.get("medae", float("nan")),
                 test_base.get("medae", float("nan")), "EXPLOITABLE" if usable else "NON EXPLOITABLE : "
                 + "; ".join(reasons))
    return {"version": mv.version, "run_id": run.info.run_id, "selected": selected, "champion": should_promote,
            **{k: meta[k] for k in ("usability", "test", "test_baseline", "test_unseen", "n_train")}}


def _should_promote(client, current, test_m: dict[str, float], usable: bool) -> bool:
    """Règle de promotion automatique :
    - pas de champion -> promotion ;
    - un modèle NON exploitable ne remplace jamais un champion exploitable ;
    - un modèle exploitable remplace un champion non exploitable, ou entraîné sans les règles économiques ;
    - sinon, promotion seulement si l'erreur de test est meilleure (comparaison indicative : les jeux de test
      peuvent différer d'un entraînement à l'autre ; `bourse ml promote` permet de trancher à la main)."""
    if current is None:
        return True
    try:
        run = client.get_run(current.run_id)
        prev_usable = run.data.tags.get("usable") == "True"
        prev = run.data.metrics.get("test_medae")
        prev_checked = "monotonic_constraints" in run.data.params  # champion entraîné sans règles économiques
    except Exception:
        return True
    if usable and not prev_checked:
        return True
    if prev_usable != usable:
        return usable
    return prev is None or bool(test_m and test_m["medae"] < prev)


def _fresh(name: str, min_sector: int, constraints: dict[str, int] | None = None):
    if name.startswith("per_sector"):
        return PerSectorModel(min_obs=min_sector, constraints=constraints)
    if name == "baseline":
        return GroupMedianBaseline()
    return make_candidates("+segments" in name, constraints)[name]


def check_monotonicity(model, X: pd.DataFrame, constraints: dict[str, int], domain: dict[str, dict[str, float]],
                       max_rows: int = 2000) -> dict[str, float]:
    """Vérification après entraînement : pour chaque KPI contraint, on l'augmente d'un quart de sa plage
    d'entraînement (p1–p99) et on mesure la part des cas où le multiple prédit varie dans le mauvais sens."""
    X = X.head(max_rows)
    base = model.predict(X)
    out = {}
    for f, sign in constraints.items():
        if not sign or f not in domain:
            continue
        rows = X[f].notna().to_numpy()
        if not rows.any():
            continue
        step = 0.25 * (domain[f]["p99"] - domain[f]["p01"]) or 0.01
        up = X.copy()
        up[f] = up[f] + step
        diff = model.predict(up) - base
        out[f] = float(np.mean((np.sign(sign) * diff[rows]) < -1e-9))
    return out
