# Databricks notebook source
# MAGIC %md
# MAGIC ### Amorçage commun (appelé par `%run ./_bootstrap` dans les autres notebooks)
# MAGIC Ajoute `src/` au chemin Python (les dépendances sont installées par `%pip` dans le notebook appelant).

# COMMAND ----------

import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    p = Path(os.getcwd()).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "bourse").exists():
            return candidate
    raise RuntimeError(f"Racine du dépôt Bourse introuvable depuis {p}")


REPO = _repo_root()
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
print(f"Dépôt : {REPO}")
