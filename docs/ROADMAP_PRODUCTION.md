# Passage en production — FMP + Databricks (recommandé) ou GCP

## Où en est-on (phase 1, local)

| Brique | Local aujourd'hui | Contrat déjà prévu pour la migration |
|---|---|---|
| Données | Parquet sous `data/` (un seul lac : application + ML) | `bourse.storage.Store` : un seul point d'accès |
| Orchestration | `bourse run` (quotidien) + `bourse ml all` (ponctuel) | commandes CLI idempotentes et relançables |
| Suivi d'expériences / registre | MLflow local (sqlite `data/ml/mlflow.db`) | `ml.registry.tracking_uri` / `registry_uri` |
| Modèle en production | alias MLflow `champion` + `gold.ml_predictions` | `load_model()` / `bourse ml promote <version>` |
| Application | Streamlit local | même code sur Databricks Apps ou Cloud Run |
| Secrets | `.env` | secret scope Databricks / Secret Manager GCP |

## Faut-il migrer maintenant ?

Statut (26/11/2026) : V1 validée en local, migration vers Databricks Free Edition préparée
(`docs/DEPLOY_DATABRICKS.md`). Raisons initiales de commencer en local :

- volume modeste : ~1 000 sociétés × 8 jeux de données ≈ 8 000 appels FMP (≈ 30 min à 300/min), quelques
  centaines de Mo de JSON bruts, quelques dizaines de Mo de Parquet ;
- entraînement de quelques minutes sur un portable (scikit-learn) ;
- itérations rapides sur les features, le protocole et l'interface, sans coût cloud.

Migrer dès que l'un de ces besoins apparaît : mise à jour quotidienne sans dépendre d'un poste allumé,
application accessible à d'autres personnes, univers de plusieurs milliers de sociétés, historique de
plusieurs versions de datasets, monitoring.

## Recommandation : Databricks

| Besoin | Databricks | GCP |
|---|---|---|
| Lakehouse versionné | Delta Lake + Unity Catalog (time travel) | GCS + BigQuery (snapshots à gérer) |
| Suivi d'expériences + registre | MLflow natif, Unity Catalog Model Registry : **aucun changement de code** (URI de suivi) | Vertex AI Experiments / Model Registry : adaptation du code d'enregistrement |
| Orchestration | Lakeflow Jobs (planification, reprises, alertes) | Cloud Scheduler + Cloud Run Jobs (ou Composer, plus lourd) |
| Application | Databricks Apps (Streamlit natif) | Cloud Run |
| Service du modèle | batch (job) ou Model Serving | batch (Cloud Run Job) ou Vertex Endpoints |
| Secrets | secret scopes (déjà lus par `bourse.config`) | Secret Manager |

Le code étant déjà construit autour de MLflow, Databricks est la cible la plus directe. GCP reste pertinent si
l'objectif est le coût minimal pour une petite application (Cloud Run facture à l'usage).

## Étapes de migration vers Databricks

Mise en œuvre pour la Free Edition : [`DEPLOY_DATABRICKS.md`](DEPLOY_DATABRICKS.md). Récapitulatif :

1. **Secrets** : `databricks secrets create-scope fmp-scope` puis clé `fmp-api-key` (déjà lue par
   `bourse.config`), et une clé pour le LLM.
2. **Stockage** : un Volume Unity Catalog (`/Volumes/<catalog>/bourse/lake`) comme `BOURSE_DATA_DIR` pour le
   bronze ; puis un « writer » Delta pour publier `core.*` et `gold.*` en tables Unity Catalog.
3. **MLflow** : `ml.registry.tracking_uri: databricks`, `registry_uri: databricks-uc`,
   `model_name: <catalog>.bourse.bourse_pfcf`. Les alias `champion` restent identiques.
4. **Jobs** (Databricks Asset Bundle, `databricks.yml`) :
   - `daily_refresh` : `bourse run` (ingestion, transformations, KPIs, actualités, prédictions) ;
   - `ml_monthly` : `bourse ml ingest` → `dataset` → `train` (promotion automatique si meilleur) ;
   - alertes e-mail en cas d'échec.
5. **Application** : Databricks Apps (`app.yaml`), lecture des tables via un SQL Warehouse.
6. **Monitoring** : contrôles qualité déjà en place (`ops.quality_report`) publiés en table ; dérive des
   features (distribution des 7 KPIs) et des erreurs du modèle comparées à la fiche d'entraînement.

## Coûts — à chiffrer avant tout déploiement

Postes : FMP (abonnement Starter en cours ; Premium si historique plus long, Ultimate pour l'international),
calcul des jobs (quelques minutes par jour), SQL Warehouse pour l'application (poste principal si allumé en
permanence : utiliser l'arrêt automatique / serverless), stockage (négligeable à ce volume), LLM (nombre
d'articles notés par jour × prix du modèle). Une estimation chiffrée sera faite avec les calculateurs de prix
au moment de la migration.
