# Databricks notebook source
# MAGIC %md
# MAGIC # Bourse — réentraînement du modèle de valorisation (mensuel)
# MAGIC Dataset point-in-time → entraînement et comparaison des modèles → enregistrement dans le registre
# MAGIC Unity Catalog (`workspace.bourse.bourse_pfcf`, alias `champion` déplacé si le modèle est meilleur)
# MAGIC → prédictions. Le paramètre `ingest` force un rechargement complet de l'univers (≈ 8 000 appels).

# COMMAND ----------

dbutils.widgets.dropdown("ingest", "non", ["non", "oui"], "Ingestion complète de l'univers")

# COMMAND ----------

# MAGIC %pip install -q duckdb pyyaml python-dotenv "scikit-learn>=1.5" "mlflow>=3.0"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_bootstrap

# COMMAND ----------

from bourse.databricks import bootstrap, publish_tables, run_cli

settings = bootstrap()
if dbutils.widgets.get("ingest") == "oui":
    run_cli("ml", "ingest")
run_cli("ml", "dataset")
run_cli("ml", "train")
run_cli("ml", "predict")
run_cli("ml", "models")

# COMMAND ----------

publish_tables(settings, tables=["ml_predictions"])
