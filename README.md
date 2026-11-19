# Bourse — analyse fondamentale à la Graham

Application d'analyse boursière inspirée de GrahamInvest, alimentée par l'API
[Financial Modeling Prep](https://site.financialmodelingprep.com/) (FMP) et mise à jour quotidiennement.

Pour chaque action suivie :

- **Résumé** : cours, verdict de valorisation, juste valeur, marge de sécurité, critères de Graham, notes sur 100 ;
- **Valorisation** : nombre de Graham, formule révisée de Graham, DCF, multiple de P/E historique (+ DCF FMP
  indicatif), avec un **simulateur** pour tester vos propres hypothèses ;
- **Critères de Graham** : les 7 critères de l'investisseur défensif (*L'investisseur intelligent*, ch. 14) ;
- **Finances** : CA, résultat net, BPA, free cash flow, marges, tableau annuel téléchargeable ;
- **Rentabilité & solidité** : ROE, ROIC, liquidité, endettement, P/E historique, Altman Z, Piotroski F ;
- **Dividendes**, **Analystes** (objectifs de cours, recommandations, estimations), **Actualités**, **Initiés**.

Et au niveau global : vue d'ensemble du marché, **screener**, **watchlist**, **actualités**, **calendrier des
résultats** et une page **Données & pipeline** (historique des runs, contrôles qualité, état de l'ingestion).

> ⚠️ Outil d'aide à l'analyse, pas un conseil en investissement.

---

## 1. Architecture

```
            ┌─────────────┐   bourse run (1×/jour)
 API FMP ──▶│  ingestion  │──▶ bronze/  JSON brut, partitionné par date d'ingestion
 (stable)   └─────────────┘
                   │  rate limiter, retries, politique de fraîcheur, reprise sur quota
                   ▼
            ┌─────────────┐
            │   silver    │──▶ silver/  staging au schéma FMP : typé, dédupliqué (upsert incrémental)
            └─────────────┘
                   ▼
            ┌─────────────┐
            │    core     │──▶ core/    modèle normalisé indépendant du fournisseur, dates de publication,
            └─────────────┘            TTM point-in-time, capitalisation quotidienne, segments, pairs
                   ▼
            ┌─────────────┐
            │    gold     │──▶ gold/    7 KPIs fondamentaux, valorisation, critères de Graham, screener
            └─────────────┘
                   ▼
            ┌─────────────┐
            │  qualité    │──▶ ops/     journal des runs, état d'ingestion, rapport qualité
            └─────────────┘
                   ▼
            Application Streamlit (lecture seule de gold/silver/ops)
```

```
├── config/settings.yaml        # univers de tickers, fréquences, hypothèses de valorisation, seuils Graham
├── .env.example                # modèle du fichier de secrets (clé API)
├── src/bourse/
│   ├── fmp/client.py           # client HTTP FMP (rate limit, retries, plan/quota)
│   ├── fmp/datasets.py         # catalogue déclaratif des endpoints ingérés
│   ├── fmp/fake.py             # faux client (données synthétiques) pour démo et tests
│   ├── ingestion.py            # API -> bronze
│   ├── transform/silver.py     # bronze -> silver
│   ├── core/normalize.py       # silver -> core (seul module qui connaît les champs FMP)
│   ├── financials/             # KPIs, dette nette, ROCE, multiples (fonctions pures testées)
│   ├── transform/kpis.py       # core -> gold.kpis
│   ├── transform/gold.py       # silver -> gold (valorisation, Graham, notes)
│   ├── analytics/              # formules de valorisation et de notation (fonctions pures)
│   ├── quality.py              # contrôles qualité
│   ├── pipeline.py             # orchestration + journal des runs
│   └── cli.py                  # commande `bourse`
├── app/streamlit_app.py        # application (pages dans app/views/)
├── tests/                      # tests unitaires, pipeline de bout en bout, tests de fumée de l'app
├── Dockerfile                  # image unique app + pipeline (Cloud Run…)
└── .github/workflows/ci.yml    # lint + tests à chaque push
```

Choix principaux :

- **Médaillon bronze/silver/gold** : le bronze garde les réponses brutes de l'API → on peut recalculer
  silver/gold à tout moment (`bourse transform --full-refresh`) sans consommer de quota.
- **Parquet local + DuckDB** pour démarrer : zéro infrastructure. Toute la lecture/écriture passe par
  `bourse.storage.Store`, ce qui rend la bascule vers Databricks (Delta) ou GCP (GCS/BigQuery) localisée.
- **Catalogue d'endpoints déclaratif** (`fmp/datasets.py`) : ajouter une donnée FMP = ajouter une ligne.
- **Économie de quota** : chaque jeu de données a une fréquence (`daily` / `weekly`) ; les cours sont chargés en
  incrémental ; un endpoint hors de votre plan est détecté une fois puis ignoré ; si le quota journalier est
  atteint, le run s'arrête proprement et **reprend le lendemain là où il s'était arrêté**.

Documentation détaillée : [`docs/PLAN.md`](docs/PLAN.md) (avancement par lots),
[`docs/DECISIONS.md`](docs/DECISIONS.md) (choix techniques et conventions financières),
[`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) (tables et définitions).

**Univers MVP** : 10 sociétés en 3 groupes de pairs — MSFT, AAPL, GOOGL, META / KO, PEP, PG / V, MA, AXP.

---

## 2. Où mettre votre clé API

**Dans un fichier `.env` à la racine du projet** (il est dans `.gitignore`, il ne sera jamais poussé sur GitHub) :

```bash
cp .env.example .env
# puis éditez .env :
FMP_API_KEY=votre_cle_ici
```

Optionnel : `ANTHROPIC_API_KEY=...` dans le même fichier active les textes générés par IA (résumé « comment
l'entreprise gagne de l'argent », puis synthèse des actualités). Ils sont générés pendant `bourse run`, mis en
cache, et clairement étiquetés dans l'application. Sans cette clé, tout le reste fonctionne.

Ne mettez jamais la clé dans `config/settings.yaml` ni dans le code. Plus tard, sur le cloud, elle ira dans un
gestionnaire de secrets (voir § 5).

---

## 3. Lancer en local (manuellement)

Prérequis : Python ≥ 3.10.

```bash
git clone https://github.com/Benjco2000/Bourse.git
cd Bourse
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -e ".[dev]"              # installe le projet + la commande `bourse`
```

### a) Essayer tout de suite, sans clé (données synthétiques)

```bash
bourse demo                                              # génère data_demo/ en quelques secondes
BOURSE_DATA_DIR=./data_demo streamlit run app/streamlit_app.py
```

Un bandeau « Mode démonstration » rappelle que les chiffres sont fictifs.

### b) Avec vos vraies données

```bash
bourse run                       # ingestion FMP + transformations + contrôles qualité
streamlit run app/streamlit_app.py
```

Ouvrez http://localhost:8501.

### Commandes utiles

| Commande | Effet |
|---|---|
| `bourse run` | pipeline complet (à lancer chaque jour) |
| `bourse run --tickers MSFT KO` | uniquement certains tickers |
| `bourse run --force` | ignore les fréquences et re-télécharge tout |
| `bourse ingest --datasets quote prices_daily` | ingestion ciblée (→ silver) |
| `bourse transform [--full-refresh]` | recalcule silver/gold depuis le bronze, **sans appel API** |
| `bourse status` | derniers runs + contrôles qualité |
| `bourse datasets` | liste des endpoints FMP ingérés |
| `bourse anomalies [MSFT]` | détail des incohérences détectées dans les états financiers |
| `bourse probe AAPL` | diagnostic : teste chaque endpoint pour un ticker et affiche ce que votre plan autorise (~26 appels) |
| `make test` / `make lint` | tests / lint |

Le suivi d'un autre ticker se fait dans `config/settings.yaml` (`universe.tickers`), puis `bourse run`.
`include_sp500: true` ajoute automatiquement les ~500 valeurs du S&P 500 (plan payant recommandé).

### Plan FMP et quotas

| Plan | Appels | Historique | Conséquence pour l'app |
|---|---|---|---|
| Basic (gratuit) | 250 / jour | 5 ans | ~19 appels/ticker au 1er chargement : 20 tickers = 2 jours. Certains endpoints sont absents (ils seront simplement ignorés et l'onglet correspondant affichera « indisponible »). |
| Starter | 300 / min | 5 ans | Tout l'univers en quelques minutes. |
| Premium | 750 / min | 30+ ans | Nécessaire pour évaluer pleinement les critères Graham sur 10 ans. |

**Indiquez votre plan dans `config/settings.yaml` (`api.plan: basic | starter | premium`)** — à passer à
`starter` dès l'abonnement souscrit (débloque le trimestriel, donc le vrai TTM). En `basic`, le
pipeline limite les états financiers à 5 exercices, saute le trimestriel, les news, les initiés et le S&P 500
(non inclus dans le plan gratuit), et les tickers hors de la liste autorisée par FMP sont simplement ignorés
(listés dans les logs et sur la page « Données & pipeline »).

Avec 5 ans d'historique, les critères « stabilité » et « croissance des bénéfices » sont marqués **◐ (historique
partiel)**. Ajustez `api.requests_per_minute` dans `settings.yaml` selon votre plan.

---

## 4. Méthodologie (résumé)

- **Juste valeur composite** = médiane des méthodes disponibles parmi `composite_methods`
  (nombre de Graham, formule de Graham, DCF, P/E historique). Le DCF de FMP est affiché à titre indicatif.
- **Nombre de Graham** = √(22,5 × BPA × actif net par action).
- **Formule révisée de Graham** = BPA × (8,5 + 2g) × 4,4 / Y — g : croissance attendue (consensus analystes si
  disponible, sinon historique 5 ans, bornée à [−5 % ; 15 %]) ; Y : taux 10 ans US + spread (≈ AAA).
- **DCF** : free cash flow par action (TTM), 5 ans à g, 5 ans de convergence vers 2,5 %, actualisé à 9 %.
- **Marge de sécurité** = (juste valeur − cours) / juste valeur → *Sous-évaluée* (≥ 20 %), *Surévaluée* (≤ −10 %).
- **Notes /100** sur 5 dimensions (valorisation, rentabilité, croissance, santé financière, dividende), barèmes
  dans `src/bourse/analytics/scoring.py`.

Toutes les hypothèses et tous les seuils sont dans `config/settings.yaml`.

Limites connues : les sociétés publiant dans une devise différente de leur devise de cotation (ADR) peuvent avoir
des ratios faussés ; les banques/assurances se prêtent mal au DCF sur free cash flow et au ratio de liquidité.

---

## 5. Prochaines étapes (on les fera ensemble)

### Étape 2 — Planifier le pipeline chaque jour et héberger l'app

Deux options, au choix :

**Option A — GCP (la plus simple, aucun changement de code)**
1. Bucket GCS pour les données, monté dans Cloud Run comme volume (`BOURSE_DATA_DIR=/data`).
2. Clé FMP dans **Secret Manager**, injectée en variable d'environnement `FMP_API_KEY`.
3. Image Docker (déjà fournie) poussée dans **Artifact Registry** (via Cloud Build).
4. **Cloud Run Job** `bourse run` + **Cloud Scheduler** chaque soir après la clôture US (ex. 23h30 Paris).
5. **Cloud Run Service** pour l'application (+ IAP ou authentification si vous voulez la garder privée).

**Option B — Databricks**
1. Clé dans un **secret scope** (`fmp-scope` / `fmp-api-key`, déjà géré par `bourse.config`).
2. Données dans un **Volume Unity Catalog** (`BOURSE_DATA_DIR=/Volumes/<catalog>/<schema>/bourse`).
3. **Job Databricks** quotidien qui exécute `bourse run` (wheel ou script), déployé avec un
   **Databricks Asset Bundle** (`databricks.yml`).
4. Gold publié aussi en **tables Delta** (SQL, Genie, dashboards) — on ajoutera un « writer » Delta.
5. Application sur **Databricks Apps** (Streamlit supporté nativement), lisant via un SQL Warehouse — on ajoutera
   un « reader » dans `app/lib/data.py`.

### Étape 3 — Industrialisation complémentaire (idées)
- Alertes (e-mail/Slack) en cas de run en échec ou de contrôle qualité en erreur.
- Authentification de l'application et watchlist par utilisateur (aujourd'hui : fichier partagé).
- Historisation quotidienne de la valorisation (évolution de la juste valeur dans le temps).
- Plus de marchés (Euronext : `MC.PA`, `AIR.PA`… selon votre plan FMP) et conversion de devises.
- Nouveaux onglets : comparaison entre concurrents, transcriptions des conférences de résultats,
  portefeuille (positions, plus-values, dividendes perçus), ETF/fonds détenant l'action.
- Pré-commit (ruff), couverture de tests, environnement de staging.
