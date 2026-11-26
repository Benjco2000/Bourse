# Databricks notebook source
# MAGIC %md
# MAGIC # Bourse — installation sur Databricks (à exécuter une fois)
# MAGIC
# MAGIC 1. Crée le schéma `workspace.bourse` et le Volume `lake` (stockage des données).
# MAGIC 2. Enregistre vos clés API dans le secret scope `bourse` (saisies dans les champs en haut du notebook).
# MAGIC 3. Vérifie que Databricks peut joindre FMP et OpenAI (accès internet sortant).
# MAGIC
# MAGIC **Après exécution, videz les champs des clés** (elles sont alors stockées dans le secret scope).

# COMMAND ----------

dbutils.widgets.text("fmp_api_key", "", "Clé FMP")
dbutils.widgets.text("openai_api_key", "", "Clé OpenAI (optionnel)")
dbutils.widgets.text("catalog", "workspace", "Catalogue")
dbutils.widgets.text("schema", "bourse", "Schéma")

# COMMAND ----------

catalog, schema = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.lake")
print(f"Schéma {catalog}.{schema} et Volume /Volumes/{catalog}/{schema}/lake prêts")

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
SCOPE = "bourse"
if SCOPE not in [s.name for s in w.secrets.list_scopes()]:
    w.secrets.create_scope(scope=SCOPE)
    print(f"Secret scope « {SCOPE} » créé")
for widget, key in (("fmp_api_key", "fmp-api-key"), ("openai_api_key", "openai-api-key")):
    value = dbutils.widgets.get(widget).strip()
    if value:
        w.secrets.put_secret(scope=SCOPE, key=key, string_value=value)
        print(f"Secret « {key} » enregistré")
present = [s.key for s in w.secrets.list_secrets(scope=SCOPE)]
print("Secrets présents :", present)
if "fmp-api-key" not in present:
    raise ValueError(
        "Clé FMP absente : saisissez-la dans le champ « Clé FMP » en haut du notebook "
        "(au premier « Run all », les champs viennent d'être créés et sont vides), "
        "puis relancez le notebook."
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ### Test de l'accès internet sortant
# MAGIC En Free Edition, l'accès sortant est limité à des domaines de confiance. Si FMP est bloqué, faites la
# MAGIC vérification LinkedIn proposée dans votre compte (elle débloque l'accès internet sortant).

# COMMAND ----------

import requests

def check(name, url, **kw):
    try:
        r = requests.get(url, timeout=15, **kw)
        print(f"{name:7s} : HTTP {r.status_code} — {'OK' if r.status_code < 400 else r.text[:120]}")
    except Exception as exc:
        print(f"{name:7s} : INACCESSIBLE ({type(exc).__name__}) — accès internet sortant bloqué ?")

fmp_key = dbutils.secrets.get(SCOPE, "fmp-api-key")
check("FMP", f"https://financialmodelingprep.com/stable/profile?symbol=AAPL&apikey={fmp_key}")
try:
    oa = dbutils.secrets.get(SCOPE, "openai-api-key")
    check("OpenAI", "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {oa}"})
except Exception:
    print("OpenAI  : pas de clé (résumés IA désactivés)")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Univers élargi
# MAGIC `config/universe_ml.csv` doit être présent dans le dépôt (committez-le depuis votre poste), sinon
# MAGIC exécutez la cellule suivante pour le construire avec le screener FMP.

# COMMAND ----------

# MAGIC %pip install -q duckdb pyyaml python-dotenv

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_bootstrap

# COMMAND ----------

from bourse.databricks import bootstrap, run_cli

settings = bootstrap()
if (REPO / "config" / "universe_ml.csv").exists():
    print("Univers élargi présent : config/universe_ml.csv")
else:
    run_cli("ml", "universe")  # écrit config/universe_ml.csv dans le dossier Git (à committer)
