# Déploiement sur Databricks (Free Edition)

Ce qui tourne sur Databricks :

| Brique | Où | Fichier du dépôt |
|---|---|---|
| Données (bronze → gold) | Volume Unity Catalog `/Volumes/workspace/bourse/lake` | `src/bourse/storage.py` (inchangé) |
| Mise à jour quotidienne | Job « Bourse — mise à jour quotidienne » (serverless) | `databricks/notebooks/01_daily_refresh.py` |
| Réentraînement mensuel | Job « Bourse — réentraînement du modèle » | `databricks/notebooks/02_ml_retrain.py` |
| Suivi des expériences | MLflow du workspace, expérience `/Shared/bourse_pfcf` | `config/settings.databricks.yaml` |
| Registre de modèles | Unity Catalog : `workspace.bourse.bourse_pfcf`, alias `champion` | idem |
| Tables pour SQL / tableaux de bord | `workspace.bourse.company_snapshot`, `kpis`, `ml_predictions`… | `bourse.databricks.publish_tables` |
| Application | Databricks Apps (Streamlit) | `app.yaml` |
| Clés API | Secret scope `bourse` (`fmp-api-key`, `openai-api-key`) | `databricks/notebooks/00_setup.py` |

Le code est le même qu'en local : `BOURSE_ENV=databricks` active `config/settings.databricks.yaml`
(registre MLflow Unity Catalog) et `BOURSE_DATA_DIR` pointe vers le Volume.

---

## 0. Avant de commencer

1. **Accès internet sortant.** En Free Edition, l'accès sortant est limité à une liste de domaines de
   confiance : FMP et OpenAI risquent d'être bloqués. La **vérification LinkedIn** proposée dans le compte
   débloque l'accès internet sortant. Faites-la avant tout (le notebook `00_setup` le vérifie).
2. **Univers élargi dans Git.** Depuis votre poste : `git add config/universe_ml.csv`, commit, push. Sinon
   `00_setup` le reconstruira (1 appel FMP) dans le dossier Git de Databricks.

## 1. Relier le dépôt

1. *Settings* (avatar en haut à droite) → *Linked accounts* → *Git integration* → GitHub (nécessaire si le
   dépôt est privé).
2. *Workspace* → *Create* → *Git folder* → URL `https://github.com/Benjco2000/Bourse` → *Create*.
   Le dossier apparaît sous `/Workspace/Users/<votre e-mail>/Bourse`.

## 2. Installation (une fois) — notebook `databricks/notebooks/00_setup`

1. Ouvrez le notebook, attachez-le au calcul **Serverless**.
2. Remplissez les champs en haut : clé FMP, clé OpenAI (optionnel).
3. *Run all*. Le notebook crée le schéma `workspace.bourse`, le Volume `lake`, le secret scope `bourse`,
   enregistre les clés et teste l'accès à FMP et OpenAI.
4. **Videz les champs des clés** (elles sont désormais dans le secret scope).

Si le test FMP affiche « INACCESSIBLE », l'accès sortant est bloqué (voir § 0).

## 3. Données initiales — deux options

**Option A (recommandée) : envoyer les données déjà téléchargées en local** (aucun appel API).
Depuis votre poste, avec la [CLI Databricks](https://docs.databricks.com/aws/en/dev-tools/cli/install) :

```powershell
databricks auth login --host https://<votre-workspace>.cloud.databricks.com
databricks fs cp -r data/bronze dbfs:/Volumes/workspace/bourse/lake/bronze
databricks fs cp data/ops/ingestion_state.parquet dbfs:/Volumes/workspace/bourse/lake/ops/ingestion_state.parquet
```

Puis lancez une fois le job quotidien (§ 4) : il reconstruit toutes les tables à partir du bronze et
n'appelle l'API que pour ce qui n'est plus à jour.

**Option B : tout télécharger depuis Databricks.** Lancez `02_ml_retrain` avec le paramètre `ingest = oui`
(≈ 10 000 appels, 40 min environ), puis le job quotidien.

Le registre MLflow local n'est pas transféré : le premier modèle Unity Catalog est créé par le job de
réentraînement (§ 4), à lancer une fois après le premier job quotidien.

## 4. Jobs planifiés

**Par l'interface** (le plus simple) — *Jobs & Pipelines* → *Create* → *Job* :

| Job | Tâche (type *Notebook*, source *Workspace*) | Calcul | Planification |
|---|---|---|---|
| Bourse — mise à jour quotidienne | `…/Bourse/databricks/notebooks/01_daily_refresh` | Serverless | 23:30 Europe/Paris, lundi–vendredi |
| Bourse — réentraînement du modèle | `…/Bourse/databricks/notebooks/02_ml_retrain` (paramètre `ingest` = `non`) | Serverless | le 1er du mois, 7:00 |

Ajoutez une notification e-mail en cas d'échec. Lancez chaque job une première fois à la main
(*Run now*) : d'abord le quotidien, puis le réentraînement.

**Ou par le bundle** : `databricks.yml` décrit les deux jobs et l'application (`databricks bundle deploy`
depuis votre poste, ou bouton *Deploy* du dossier Git si disponible).

## 5. Application

1. *Compute* → *Apps* → *Create app* → *Custom* → nom `bourse` → *Create*.
2. *Deploy* → chemin du code source : le dossier Git `/Workspace/Users/<votre e-mail>/Bourse`.
   `app.yaml` (racine du dépôt) lance Streamlit ; `requirements.txt` est installé automatiquement.
3. **Droits du principal de service de l'application** (affiché dans l'onglet *Authorization* de l'app) —
   *Catalog* → `workspace` → `bourse` → *Permissions* → *Grant* :
   - `USE CATALOG` sur `workspace`, `USE SCHEMA` sur `workspace.bourse` ;
   - `READ VOLUME` sur `workspace.bourse.lake` (l'app copie les tables du Volume au démarrage puis toutes
     les 30 min) ;
   - `EXECUTE` sur le modèle `workspace.bourse.bourse_pfcf` (simulateur de la page Valorisation IA).
4. Ouvrez l'URL de l'application.

En Free Edition, une application s'arrête automatiquement 24 h après son démarrage ou son dernier
déploiement : relancez-la depuis *Compute → Apps* (*Start*).

## 6. Mettre à jour

- Code : `git push` depuis votre poste → dans Databricks, dossier Git → *Pull*. Les jobs utilisent la
  nouvelle version au run suivant ; pour l'application, *Deploy* à nouveau.
- Modèle : `02_ml_retrain` (mensuel). Retour arrière : dans un notebook,
  `run_cli("ml", "promote", "<version>")`, ou alias `champion` dans *Catalog → Models*.

## 7. Limites connues

- Free Edition : calcul serverless soumis à quota et à la politique d'usage équitable, pas de SLA, usage
  non commercial.
- Watchlist et choix de concurrents faits dans l'application déployée : conservés le temps de vie de
  l'application seulement (stockage local de l'app).
- Les notebooks installent `mlflow`, `scikit-learn`, `duckdb`… à chaque exécution de job (≈ 1–2 min).
