import json

import pandas as pd
import streamlit as st
from lib import data

st.title("Données & pipeline")
path, demo = data.data_location()
st.caption(f"Dossier de données : `{path}`" + (" — **mode démonstration**" if demo else ""))

runs = data.table("ops", "pipeline_runs")
qa = data.table("ops", "quality_report")
state = data.table("ops", "ingestion_state")

st.subheader("Contrôles qualité du dernier run")
if qa.empty:
    st.caption("Aucun contrôle exécuté.")
else:
    icons = {"ok": "✅", "warn": "⚠️", "error": "❌"}
    for r in qa.itertuples():
        st.markdown(f"{icons.get(r.status, '')} **{r.check}** — {r.detail}")

st.subheader("Historique des runs")
if runs.empty:
    st.caption("Aucun run.")
else:
    r = runs.sort_values("started_at", ascending=False).head(30).copy()
    r["appels API"] = r["summary"].map(lambda s: json.loads(s).get("ingest", {}).get("api_calls"))
    st.dataframe(r[["run_id", "started_at", "duration_s", "status", "steps", "appels API", "demo"]],
                 hide_index=True, width="stretch",
                 column_config={"started_at": st.column_config.DatetimeColumn("Début (UTC)",
                                                                              format="DD/MM/YYYY HH:mm"),
                                "duration_s": st.column_config.NumberColumn("Durée (s)")})

st.subheader("État de l'ingestion par jeu de données")
if not state.empty:
    agg = state.groupby("dataset").agg(
        appels=("key", "count"),
        ok=("status", lambda s: int((s == "ok").sum())),
        erreurs=("status", lambda s: int((s == "error").sum())),
        hors_plan=("status", lambda s: int((s == "restricted").sum())),
        dernier_succes=("last_success_at", "max"),
        lignes=("rows", "sum"),
    ).reset_index()
    st.dataframe(agg, hide_index=True, width="stretch",
                 column_config={"dernier_succes": st.column_config.DatetimeColumn("Dernier succès",
                                                                                  format="DD/MM/YYYY HH:mm")})
    errors = state[state["status"] != "ok"]
    if not errors.empty:
        with st.expander(f"Détail des {len(errors)} appels en erreur / hors plan"):
            st.dataframe(errors[["dataset", "key", "status", "error", "last_attempt_at"]], hide_index=True,
                         width="stretch")

st.subheader("Tables disponibles")
rows = []
for layer in ("silver", "gold"):
    for p in sorted((path / layer).glob("*.parquet")):
        rows.append({"couche": layer, "table": p.stem, "taille (Ko)": round(p.stat().st_size / 1024, 1),
                     "modifiée": pd.Timestamp(p.stat().st_mtime, unit="s")})
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
