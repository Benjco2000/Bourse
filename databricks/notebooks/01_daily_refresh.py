# Databricks notebook source
# MAGIC %md
# MAGIC # Bourse — mise à jour quotidienne
# MAGIC Ingestion FMP (incrémentale) → silver → core → gold (KPIs, multiples, actualités, prédictions du modèle
# MAGIC champion) → contrôles qualité → publication des tables gold dans Unity Catalog.

# COMMAND ----------

# MAGIC %pip install -q duckdb pyyaml python-dotenv "scikit-learn>=1.5" "mlflow>=3.0"

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./_bootstrap

# COMMAND ----------

from bourse.databricks import bootstrap, publish_tables, run_cli

settings = bootstrap()
run_cli("run")

# COMMAND ----------

publish_tables(settings)
run_cli("status")
