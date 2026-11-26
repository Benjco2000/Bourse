"""Page 4 — Estimation de la valorisation par Machine Learning (P/FCF attendu)."""

import dataclasses
import json

import pandas as pd
import streamlit as st
from lib import charts, data, fmt, ui

from bourse.config import get_settings
from bourse.financials.kpis import KPI_META

data.demo_banner()
sym = st.session_state.get("symbol") or data.companies()["ticker"].iloc[0]
st.title(f"Valorisation IA — {sym}")
st.caption("Un modèle estime le multiple **Price / FCF** que le marché a historiquement attribué à des sociétés "
           "aux fondamentaux comparables (7 KPIs, secteur, taille), puis le convertit en prix par action. "
           "Outil d'aide à l'analyse : ni une valeur intrinsèque certaine, ni une recommandation.")

LABELS = {k: v[0] for k, v in KPI_META.items()} | {"sector": "Secteur", "cap_bucket": "Taille (capitalisation)"}
preds = data.table("gold", "ml_predictions")
if preds.empty:
    st.info("Aucun modèle n'a encore été entraîné. Commandes : `bourse ml all` (univers, ingestion, dataset, "
            "entraînement, prédictions) — voir le README, section « Valorisation IA ».", icon=":material/info:")
    st.stop()


@st.cache_resource(show_spinner="Chargement du modèle…")
def get_bundle(data_dir: str, version: str):
    from bourse.ml.registry import load_model

    settings = dataclasses.replace(get_settings(), data_dir=data.data_location()[0])
    return load_model(settings, version=version)


row = preds[preds["ticker"] == sym]
version = str(preds["model_version"].iloc[0])
try:
    bundle = get_bundle(str(data.data_location()[0]), version)
except Exception as exc:  # mlflow absent ou registre déplacé
    bundle = None
    st.warning(f"Modèle v{version} non chargeable ({exc}) : seules les prédictions enregistrées sont affichées.")
meta = bundle.meta if bundle else {}

tab_company, tab_model = st.tabs(["Estimation pour la société", "Fiche du modèle"])

# ================================================================== estimation
with tab_company:
    usable = bool(preds["model_usable"].iloc[0])
    if not usable:
        reasons = meta.get("usability", {}).get("reasons") or [str(row["status_reason"].iloc[0])] if not row.empty \
            else []
        st.error("**Modèle non exploitable statistiquement — aucun prix théorique n'est affiché.**  \n"
                 + "  \n".join(f"• {r}" for r in reasons), icon=":material/block:")
    if row.empty:
        st.info("Pas de prédiction pour cette société.")
        st.stop()
    r = row.iloc[0].to_dict()
    status = r["status"]
    labels = {"ok": None, "insufficient_inputs": "Données d'entrée trop incomplètes",
              "not_applicable": "Méthode non applicable", "out_of_domain": "Société hors du domaine couvert",
              "model_not_usable": None}
    if status not in ("ok", "model_not_usable"):
        st.warning(f"**{labels.get(status, status)}** : {r.get('status_reason')}. Aucun prix théorique affiché.",
                   icon=":material/warning:")

    ccy = (data.table("gold", "company_snapshot").set_index("ticker").get("trading_currency", pd.Series())
           .get(sym)) or ""
    if status == "ok":
        k = st.columns(4)
        ui.kpi(k[0], "P/FCF estimé (central)", f"{fmt.num(r['pfcf_mid'], 1)} x")
        ui.kpi(k[1], "Fourchette (80 %)", f"{fmt.num(r['pfcf_low'], 1)} – {fmt.num(r['pfcf_high'], 1)} x")
        ui.kpi(k[2], "Prix théorique par action", fmt.money(r["price_mid"], ccy))
        gap = r.get("gap_vs_price")
        ui.kpi(k[3], "Écart vs cours actuel", fmt.pct(gap, 0, signed=True) if gap is not None else "N/A")
        st.caption(f"Fourchette de prix : {fmt.money(r['price_low'], ccy)} – {fmt.money(r['price_high'], ccy)} · "
                   f"cours actuel {fmt.money(r['last_close'], ccy)} · P/FCF actuel {fmt.num(r['current_pfcf'], 1)} x. "
                   "Prix théorique = P/FCF estimé × FCF TTM / actions diluées.")
    elif pd.notna(r.get("pfcf_mid")):
        with st.expander("Sortie brute du modèle (à titre de démonstration du pipeline, non exploitable)"):
            st.markdown(f"P/FCF estimé : **{fmt.num(r['pfcf_mid'], 1)} x** (fourchette "
                        f"{fmt.num(r['pfcf_low'], 1)} – {fmt.num(r['pfcf_high'], 1)} x) · P/FCF actuel : "
                        f"{fmt.num(r.get('current_pfcf'), 1)} x")

    st.subheader("Facteurs explicatifs")
    contrib = json.loads(r.get("contributions") or "{}")
    if contrib:
        st.plotly_chart(charts.contributions_bars(contrib, LABELS), width="stretch", config={"displayModeBar": False})
        st.caption("Pour chaque KPI : écart de P/FCF estimé entre la valeur de la société et la médiane des "
                   "sociétés d'entraînement, les autres variables restant fixes. Ce sont des **associations "
                   "apprises par le modèle**, pas des relations de cause à effet.")

    feats = json.loads(r.get("features") or "{}")
    domain = meta.get("domain", {})
    issues = json.loads(r.get("domain_issues") or "[]")
    table = pd.DataFrame([{
        "KPI": LABELS.get(f, f),
        "Valeur de la société": fmt.pct(v, 1) if v is not None and f != "net_debt_to_fcf" else
        (f"{fmt.num(v, 1)} ans" if v is not None else "N/A"),
        "Médiane d'entraînement": (fmt.pct(domain[f]["median"], 1) if f != "net_debt_to_fcf" else
                                   f"{fmt.num(domain[f]['median'], 1)} ans") if f in domain else "—",
        "Dans la plage d'entraînement (p1–p99)": "—" if v is None or f not in domain else
        ("oui" if domain[f]["p01"] <= v <= domain[f]["p99"] else "non"),
    } for f, v in feats.items()])
    st.dataframe(table, hide_index=True, width="stretch")
    if issues:
        st.caption("Points d'attention : " + " ; ".join(issues))
    st.caption(f"Données au {pd.Timestamp(r['as_of']):%d/%m/%Y} · modèle v{r['model_version']} entraîné le "
               f"{pd.Timestamp(r['model_trained_at']):%d/%m/%Y} · prédiction du "
               f"{pd.Timestamp(r['predicted_at']):%d/%m/%Y}." if pd.notna(r.get("as_of")) else "")

    if bundle is not None and feats:
        with st.expander("Simulateur : et si les fondamentaux changeaient ?", icon=":material/tune:"):
            st.caption("Le modèle enregistré est rechargé (`load_model`) et appliqué à vos hypothèses ; il n'est "
                       "pas réentraîné.")
            from bourse.ml.predict import predict_frame

            cols = st.columns(2)
            new = {}
            for i, (f, v) in enumerate(feats.items()):
                default = float(v) if v is not None else float(domain.get(f, {}).get("median", 0.0))
                lo = float(domain.get(f, {}).get("p01", default - 1))
                hi = float(domain.get(f, {}).get("p99", default + 1))
                lo, hi = min(lo, default), max(hi, default)
                new[f] = cols[i % 2].slider(LABELS.get(f, f), lo, hi, default, (hi - lo) / 100 or 0.01,
                                            key=f"sim_{sym}_{f}")
            sector = r.get("sector") or "Inconnu"
            snap = data.table("gold", "company_snapshot")
            bucket = snap.set_index("ticker").get("cap_bucket", pd.Series()).get(sym)
            X = pd.DataFrame([{**new, "sector": sector, "cap_bucket": bucket}])
            p = predict_frame(bundle, X).iloc[0]
            st.metric("P/FCF estimé avec ces hypothèses", f"{fmt.num(p['pfcf_mid'], 1)} x",
                      delta=f"{fmt.num(p['pfcf_mid'] - r['pfcf_mid'], 1)} x vs situation actuelle")

# ================================================================== fiche modèle
with tab_model:
    if not meta:
        st.info("Métadonnées du modèle indisponibles.")
        st.stop()
    u = meta.get("usability", {})
    st.markdown(f"**Modèle v{bundle.version}** — `{meta['selected_model']}` · entraîné le "
                f"{pd.Timestamp(meta['trained_at']):%d/%m/%Y %H:%M} · code `{meta.get('git_sha')}`")
    st.markdown((":green-badge[:material/check_circle: Exploitable à titre indicatif]" if u.get("usable") else
                 ":red-badge[:material/block: Non exploitable statistiquement]")
                + ("" if u.get("usable") else " — " + " ; ".join(u.get("reasons", []))))
    k = st.columns(4)
    ui.kpi(k[0], "Observations (entraînement)", fmt.num(meta["n_train"], 0))
    ui.kpi(k[1], "Sociétés", fmt.num(meta["n_companies"], 0))
    ui.kpi(k[2], "Période", f"{meta['period'][0][:4]} – {meta['period'][1][:4]}")
    ui.kpi(k[3], "Couverture de la fourchette 80 %", fmt.pct(meta.get("test_interval_coverage"), 0)
           if meta.get("test_interval_coverage") is not None else "N/A")

    st.subheader("Performances")
    t, b, un = meta.get("test", {}), meta.get("test_baseline", {}), meta.get("test_unseen", {})
    perf = pd.DataFrame([
        {"Jeu": "Test — modèle retenu", **t}, {"Jeu": "Test — baseline (médiane secteur × taille)", **b},
        {"Jeu": "Test — sociétés jamais vues", **un}])
    if not perf.empty and "medae" in perf:
        perf = perf.rename(columns={"mae": "MAE (x)", "medae": "MedAE (x)", "rmse": "RMSE (x)",
                                    "median_rel_error": "Erreur relative médiane", "mae_log": "MAE (log)", "n": "N"})
        st.dataframe(perf, hide_index=True, width="stretch", column_config={
            "Erreur relative médiane": st.column_config.NumberColumn(format="percent"),
            **{c: st.column_config.NumberColumn(format="%.2f") for c in ("MAE (x)", "MedAE (x)", "RMSE (x)",
                                                                         "MAE (log)")}})
    st.caption("Erreurs mesurées sur le multiple P/FCF. Test = période la plus récente, jamais utilisée pour choisir "
               "le modèle. « Sociétés jamais vues » : sociétés exclues de l'entraînement, évaluées sur la même "
               "période de test.")
    val = pd.DataFrame(meta.get("val_scores", {})).T.reset_index().rename(columns={"index": "Modèle"})
    if not val.empty:
        st.markdown("**Comparaison des candidats (validation)** — avec / sans variables de segment, par secteur")
        val["Retenu"] = val["Modèle"].eq(meta["selected_model"]).map({True: "★", False: ""})
        st.dataframe(val[["Retenu", "Modèle", "medae", "median_rel_error", "mae", "n"]].rename(columns={
            "medae": "MedAE (x)", "median_rel_error": "Erreur relative médiane", "mae": "MAE (x)", "n": "N"}),
            hide_index=True, width="stretch", column_config={
                "MedAE (x)": st.column_config.NumberColumn(format="%.2f"),
                "MAE (x)": st.column_config.NumberColumn(format="%.2f"),
                "Erreur relative médiane": st.column_config.NumberColumn(format="percent")})
        st.caption("Règle de choix : meilleure MedAE de validation ; un modèle plus simple à moins de 2 % est "
                   "préféré.")
    mono = meta.get("monotonic_constraints")
    if mono:
        viol = meta.get("monotonicity_violations", {})
        st.markdown("**Règles économiques imposées au modèle** (vérifiées après l'entraînement)")
        st.dataframe(pd.DataFrame([{
            "KPI": LABELS.get(f, f),
            "Règle": {1: "↑ KPI ⇒ multiple ↑ ou =", -1: "↑ KPI ⇒ multiple ↓ ou =", 0: "libre"}[int(c)],
            "Cas en infraction": fmt.pct(viol.get(f), 1) if f in viol else "—"} for f, c in mono.items()]),
            hide_index=True, width="stretch")
        st.caption("Contraintes de monotonie appliquées à l'entraînement (Gradient Boosting, Random Forest, Ridge "
                   "à signes imposés) ; la vérification augmente chaque KPI d'un quart de sa plage et contrôle le sens "
                   "de variation du multiple estimé. Modifiables dans `config/settings.yaml` (`ml.monotonic_constraints`).")
    imp = pd.DataFrame(meta.get("importance", []))
    if not imp.empty:
        st.markdown("**Importance des variables** (permutation sur le test : dégradation de l'erreur quand la "
                    "variable est mélangée)")
        st.plotly_chart(charts.importance_bars(imp, LABELS), width="stretch", config={"displayModeBar": False})

    st.subheader("Données d'entraînement")
    split = meta.get("split", {})
    st.markdown(" · ".join(f"**{k}** : {v[0]} → {v[1]}" for k, v in split.items() if v))
    card = meta.get("dataset_card", {})
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Secteurs**")
        st.dataframe(pd.Series(meta.get("sectors", {}), name="observations"), width="stretch")
    with c2:
        st.markdown("**Exclusions**")
        st.dataframe(pd.Series(card.get("exclusions", {}), name="observations"), width="stretch")
    st.subheader("Limites")
    st.markdown("""
- **Cible = niveau de valorisation du marché**, pas une valeur intrinsèque : le modèle reproduit les multiples
  passés, y compris leurs excès.
- **Biais du survivant** : l'univers est constitué des sociétés cotées aujourd'hui ; les sociétés radiées
  (faillites, rachats) en sont absentes, ce qui peut flatter les multiples historiques.
- **Classification sectorielle actuelle** appliquée à tout l'historique.
- **Marché US uniquement** (couverture du plan FMP Starter).
- FCF ≤ 0 : méthode non applicable ; P/FCF hors [1 ; 150] exclus de l'entraînement.
""")
