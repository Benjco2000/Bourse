# Décisions techniques et métier

Chaque décision : contexte → choix → raison / alternative écartée.

## Architecture

**D1 — Couches bronze / silver / core / gold / ops.**
- `bronze` : réponses API brutes (JSON), partitionnées par date d'ingestion → retraitement sans appel API.
- `silver` : staging au schéma du fournisseur (snake_case, typé, dédupliqué, upsert incrémental).
- `core` : **modèle normalisé indépendant du fournisseur** (contrats décrits dans `DATA_DICTIONARY.md`).
  Seul `core/normalize.py` connaît les noms de champs FMP. Un autre fournisseur n'a qu'à produire `core.*`.
- `gold` : métriques servies à l'application et au ML (KPIs, valorisation...).
- `ops` : état d'ingestion, journal des runs, contrôles qualité.

**D2 — Parquet + DuckDB en local.** Zéro infrastructure, format colonne portable vers Delta / BigQuery.
Écritures atomiques (fichier temporaire + rename) pour que l'app lise pendant que le pipeline écrit.
Alternative écartée : SQLite (moins adapté à l'analytique, pas de lecture Parquet native).

**D3 — Streamlit pour l'interface.** Meilleur compromis rapidité / évolutivité pour un outil d'analyse ;
déployable tel quel sur Databricks Apps ou Cloud Run. Alternative écartée : FastAPI + React (deux briques,
MVP nettement plus long).

**D4 — Connecteur FMP isolé** (`fmp/client.py` + catalogue déclaratif `fmp/datasets.py`) : limiteur de
débit, retries avec backoff, détection des refus de plan **par ticker**, arrêt propre sur quota journalier
et reprise au run suivant. La clé n'est lue que côté serveur (`.env`, secret scope Databricks).

**D5 — Univers MVP en groupes de pairs** (tech : MSFT, AAPL, GOOGL, META ; consommation : KO, PEP, PG ;
paiements : V, MA, AXP) pour que le tableau des concurrents repose sur des données réellement ingérées.
Les groupes sont configurables (`universe.peer_groups`) et prioritaires sur l'endpoint `stock-peers` de FMP.

## Conventions financières

**F1 — Montants.** Unités monétaires brutes (`unit = units`) dans la devise de publication
(`reported_currency`). Aucune conversion de devise implicite : la devise de cotation (`trading_currency`)
est conservée séparément et toute comparaison entre devises différentes sera signalée.

**F2 — Signes.** Tous les décaissements sont stockés en **montants positifs** : `capex`, `dividends_paid`,
`share_repurchases`, `acquisitions` (net des cessions). `net_debt_issuance` et `net_change_in_cash`
gardent le signe publié (positif = entrée de cash).

**F3 — FCF = OCF − CAPEX** (CAPEX = investissements corporels publiés, en valeur absolue). Le FCF publié par
FMP n'est pas utilisé, pour garantir une définition unique.

**F4 — Marge brute = (CA − coût des ventes) / CA.** Si le coût des ventes est absent, on reprend la marge
brute publiée. S'il vaut 0 (cas fréquent des sociétés financières, ex. V, MA, AXP), la marge brute est
calculée mais marquée **orange** (peu significative).

**F5 — Dette nette = dette financière totale − (trésorerie et équivalents + placements court terme).**
Écart volontaire avec la définition stricte « trésorerie et équivalents » : pour des sociétés comme GOOGL
ou AAPL, l'essentiel du cash est placé à court terme, l'exclure surestimerait fortement la dette nette.
La dette inclut ce que FMP classe dans `totalDebt` (y compris, selon les cas, les dettes locatives).

**F6 — ROCE = NOPAT / capitaux employés**, NOPAT = EBIT × (1 − taux d'IS effectif), taux effectif =
impôt / résultat avant impôt borné à [0 ; 50 %], 21 % par défaut si non calculable (perte, donnée absente).
Capitaux employés = actif total − passif courant. CE ≤ 0 → N/A. ROCE moyen = moyenne des ROCE annuels
valides sur les 5 derniers exercices ; moins de 5 valeurs → orange.

**F7 — CAGR** : `(fin/début)^(1/années) − 1`, calculé entre exercices fiscaux réels (pas d'hypothèse
d'année calendaire). Valeur de départ ou d'arrivée ≤ 0 → N/A + orange (non interprétable). Historique
plus court que 5 ans → calculé sur la période disponible, affichée, et marqué orange.

**F8 — Nombre d'actions** : nombre moyen pondéré d'actions **diluées** des comptes annuels. Variation
cumulée non annualisée. Une variation > +80 % ou < −45 % est signalée comme possible split non ajusté
(orange) au lieu d'être interprétée.

**F9 — Dette nette / FCF** : dette nette du dernier bilan publié / FCF TTM. FCF ≤ 0 → N/A (non
interprétable). Dette nette négative → ratio négatif affiché comme « trésorerie nette » (favorable).

**F10 — Multiples « Price / X » = capitalisation boursière / agrégat TTM** (multiples de fonds propres,
jamais d'Enterprise Value). Dénominateur ≤ 0 → N/A avec explication.

## Temporalité (anti look-ahead)

**T1 — Date de disponibilité** d'un état financier = date d'acceptation SEC (`acceptedDate`), sinon date de
dépôt (`filingDate`), sinon fin de période + 90 jours (annuel) / 45 jours (trimestriel), avec le drapeau
`availability_estimated`.

**T2 — TTM** = somme de 4 trimestres **consécutifs** (écarts de 80 à 100 jours entre fins de trimestre) pour
les flux ; postes de bilan et nombre d'actions = dernier trimestre. Disponible à la date du trimestre le plus
récent. Repli : dernier exercice annuel (méthode `annual`, signalée « TTM trimestriel indisponible »).
`ttm_as_of(ticker, date)` ne renvoie que des agrégats publiés à cette date.

**T3 — Capitalisation quotidienne** : endpoint FMP `historical-market-capitalization` si disponible ; sinon
**approximation** cours de clôture × actions diluées du dernier état publié à la date (point-in-time),
signalée dans `source` et dans les contrôles qualité. Limite : ne capture pas les variations d'actions entre
deux publications ; sensible aux splits si le cours et le nombre d'actions ne sont pas ajustés de la même façon.

## Qualité des données

**Q1 — Signaler, ne pas corriger.** Les incohérences (marge brute publiée ≠ CA − coût des ventes, bilan non
équilibré, CA ≤ 0, actions manquantes...) sont stockées dans `quality_flags` et remontées dans le rapport
qualité ; les calculs concernés passent en N/A ou orange, jamais en valeur corrigée silencieusement.

**Q2 — Absent ≠ zéro.** Toutes les fonctions de calcul renvoient N/A sur donnée manquante.

## Page « Vue globale » (lot 4)

**P1 — Sélection de la société dans la barre latérale**, partagée par toutes les pages (et l'URL
`?symbol=MSFT`, pour pouvoir partager un lien). Recherche par ticker ou par nom dans la liste.

**P2 — `gold.company_snapshot` calculé dans le pipeline**, pas dans la page : capitalisation la plus
récente, TTM disponible à cette date, multiples P/S, P/OCF, P/FCF avec la raison de chaque N/A, et un
niveau de qualité (`complete` / `partial` / `degraded`) avec ses explications, affiché en tête de page.

**P3 — Devise.** Si la devise de cotation diffère de celle des comptes, les multiples ne sont pas calculés
(N/A « devises différentes ») plutôt que de mélanger des devises sans conversion.

**P4 — Résumé IA « comment l'entreprise gagne de l'argent ».** Généré dans le pipeline (jamais à
l'ouverture d'une page) par un LLM (OpenAI ou Anthropic selon `llm.provider`, modèle configurable), uniquement à partir de la
description FMP et des segments publiés, avec consigne de ne rien inventer. Mis en cache par empreinte des
entrées : il n'est régénéré que si la description, les segments ou le modèle changent. Affiché avec un
badge « Généré par IA », séparé de la description sourcée. Sans la clé du fournisseur (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`), rien n'est généré.

**P5 — Concurrents.** Par défaut, le groupe de pairs de la configuration (3 au maximum) ; l'utilisateur
peut les modifier, le choix est enregistré par société (`data/app/peer_overrides.json`) et réversible.

**P6 — Cartes KPI.** Statut porté par la couleur **et** une icône + libellé (accessibilité). Mini-courbe
de la série annuelle sous-jacente (10 derniers exercices), seuil et période toujours affichés.

## Pages 2, 3 et 5

**V1 — Historique des multiples (`gold.valuation_history`).** Numérateur : capitalisation du jour.
Dénominateur : dernier TTM **publié** à cette date (`merge_asof` sur `available_date`), trimestriel
prioritaire sur l'annuel pour une même fin de période. TTM dont la période est close depuis plus de 15 mois :
périmé, multiple non calculé. Dénominateur ≤ 0 : N/A (trou dans la courbe, part non calculable affichée).
Affichage quotidien jusqu'à 3 ans, hebdomadaire au-delà. Médiane et quartiles calculés sur la période
affichée. Échelle verticale bornée à Q3 + 3 × IQR quand des valeurs extrêmes (dénominateur proche de 0)
écraseraient la courbe ; le nombre de points hors échelle est indiqué (les données ne sont pas modifiées).

**N1 — Actualités.** Endpoint `news/stock`, paginé (100 par page, 10 pages max), premier chargement sur
`history.news_history_days` (365 j, limite du plan Starter), puis incrémental. Déduplication : même titre
normalisé publié à moins de 3 jours d'intervalle → un seul article « principal », les reprises sont comptées.
Pertinence : 1,0 si le ticker ou le nom de la société figure dans le titre, sinon 0,6.

**N2 — Sentiment par article** dans [−1 ; +1] : LLM par lots de 25 (sortie JSON), mis en cache par URL,
plafonné à `llm.max_articles_scored_per_run` articles par run (les suivants le sont aux runs suivants) ; à
défaut, lexique financier minimal (inspiré de Loughran-McDonald), méthode tracée article par article.
Agrégation hebdomadaire = moyenne pondérée par la pertinence, avec le nombre d'articles affiché à part.
Classe : positif si > 0,15, négatif si < −0,15, sinon neutre (configurable).

**N3 — Synthèse IA** sur les 30 derniers jours (40 articles max, triés par pertinence puis date) : thèmes,
positifs, négatifs, risques, sentiment, confiance, et identifiants des articles justificatifs (vérifiés).
Moins de 3 articles → « données insuffisantes », pas d'appel LLM. Mise en cache sur l'ensemble des URLs.

**C1 — Cascade de trésorerie (page 5).** OCF → CAPEX → FCF → rachats, dividendes, acquisitions nettes,
dette nette, émissions d'actions → « autres flux / réconciliation » → variation de trésorerie publiée.
Un poste non publié n'est jamais mis à zéro : il est exclu et le libellé de la réconciliation le signale.
La SBC n'est pas dans la cascade (charge non décaissée) : « FCF après SBC » est présenté à part comme
indicateur analytique. Effet des rachats : actions rachetées ≈ montant / cours moyen de l'exercice,
comparé à la variation nette des actions diluées moyennes ; l'écart approxime les émissions.
Rendements (dividende, rachats) calculés sur la capitalisation actuelle : indicatifs pour une période passée.

## Machine Learning (page 4)

**M1 — Univers d'entraînement.** Le plan FMP Starter ne couvre que les actions US (CAC 40, Nikkei… exigent le
plan Ultimate). Univers = screener FMP : actions US actives (NYSE, NASDAQ), capitalisation > 2 Md$, les 1 000
plus grandes, hors banques, assurances, REIT, gestion d'actifs (le P/FCF n'y a pas de sens économique), plus
les sociétés suivies dans l'application. Liste figée dans `config/universe_ml.csv` (versionnable). Repli : CSV
fourni à la main si le screener est indisponible.

**M2 — Lac ML séparé** (`data/ml`) : mêmes couches et même code que l'application, limité aux jeux de données
utiles (profil, états financiers annuels et trimestriels, capitalisation historique) pour maîtriser le quota.

**M3 — Dataset point-in-time.** Observation = (société, fin de trimestre t). Features = les 7 KPIs calculés
par la même fonction que la page 1, avec uniquement les états publiés au plus tard à t ; un KPI « 5 ans »
calculé sur moins de 5 ans, ou une variation d'actions suspecte (split), devient manquant. + secteur et
tranche de capitalisation à t. Cible = log(P/FCF) observé à t (capitalisation(t) / FCF TTM publié). Exclusions
comptées dans la fiche du dataset : FCF ≤ 0 (cible non définie), P/FCF hors [1 ; 150], TTM périmé, moins de
4 KPIs.

**M4 — Protocole d'évaluation.** Split chronologique train (≤ 2020) / validation (2021 S2 – 2022) / test
(≥ 2023 S2), avec 6 mois d'embargo entre blocs (les fenêtres de 5 ans se recouvrent fortement d'un trimestre à
l'autre). 20 % des sociétés sont exclues de l'entraînement et évaluées sur la période de test (« sociétés
jamais vues »). Repli par quantiles de dates si l'historique ne couvre pas les dates configurées.
Sélection sur la validation (MedAE du multiple), un modèle plus simple à moins de 2 % est préféré ; le
test n'est utilisé qu'une fois. Modèle final réentraîné sur train + validation.

**M5 — Candidats.** Baseline = médiane par secteur × taille ; Ridge ; Random Forest ; HistGradientBoosting,
chacun sans puis avec variables de segment ; modèle par secteur uniquement si un secteur dépasse
`min_sector_obs` observations. Robustesse : cible en log, winsorisation des features aux quantiles 1–99 %
apprise sur l'entraînement, valeurs manquantes imputées (avec indicateur) ou gérées nativement (HGB).

**M5 bis — Règles économiques (contraintes de monotonie).** Sans contrainte, le modèle apprenait des
relations contre-intuitives (ex. plus de croissance du FCF ⇒ multiple plus bas), en partie mécaniques : la
cible P/FCF et plusieurs KPIs partagent le FCF au dénominateur (un FCF qui vient de bondir fait à la fois
monter sa croissance et baisser le P/FCF observé). On impose donc le sens économique : croissance du CA et du
FCF, ROCE, marges ⇒ multiple ↑ ; dilution et dette nette / FCF ⇒ multiple ↓ (`ml.monotonic_constraints`).
Mise en œuvre : `monotonic_cst` du Gradient Boosting et de la Random Forest ; Ridge à signes imposés
(coefficients positifs sur des variables multipliées par leur signe, variables libres dédoublées en ±x).
Vérification après chaque entraînement (variation d'un quart de la plage p1–p99 de chaque KPI) : plus de
0,5 % d'infractions ⇒ modèle non exploitable. Contrepartie : une précision brute parfois légèrement
inférieure, au profit d'estimations cohérentes et explicables.

**M6 — Incertitude et explications.** Fourchette 80 % = quantiles 10/90 % des résidus de validation (en
log), couverture mesurée sur le test. Importance globale = permutation sur le test. Explication locale =
effet de chaque KPI par rapport à la médiane d'entraînement, toutes choses égales par ailleurs (association,
pas causalité).

**M7 — Garde-fous.** Modèle « non exploitable » (aucun prix affiché) si < 5 000 observations
d'entraînement, < 300 sociétés, ou pas meilleur que la baseline sur le test. Par société : pas de prix si FCF
≤ 0, moins de 5 KPIs calculables, secteur absent de l'entraînement ou ≥ 2 KPIs hors de la plage 1–99 %.

**M8 — Cycle de vie.** MLflow local (sqlite + dossier d'artefacts) : paramètres, métriques de tous les
candidats, version du code, période, features, fiche du dataset, artefacts (comparaison, importance,
prédictions de test). Chaque entraînement crée une version du modèle `bourse_pfcf` ; l'alias `champion` est
déplacé automatiquement selon une règle prudente : jamais vers un modèle non exploitable si le champion est
exploitable, toujours vers un modèle exploitable si le champion ne l'est pas, sinon si l'erreur de test est
meilleure (comparaison indicative, les jeux de test pouvant différer) — ou forcé / empêché (`--promote` /
`--no-promote`) ; `bourse ml promote <v>` permet le retour arrière.
« Déploiement » = prédictions batch (`gold.ml_predictions`) recalculées à chaque `bourse run`, et
`load_model()` dans l'application pour le simulateur. Sérialisation cloudpickle (modèles produits localement
par ce pipeline uniquement).

**M9 — Biais connus.** Survivant (univers = sociétés cotées aujourd'hui), classification sectorielle actuelle
appliquée au passé, cible = niveau de valorisation du marché (et non valeur intrinsèque).

## Univers élargi et déploiement (Databricks)

**U1 — Deux niveaux de sociétés.** « Suivies » (`universe.tickers`) : tous les jeux de données (actualités,
cotation, analystes, dividendes…). « Univers élargi » (`extended_universe_file`, ≈ 1 000 sociétés) : jeu
réduit (`extended_datasets` : profil, états financiers annuels et trimestriels, capitalisation, segments),
suffisant pour les pages 1, 2, 4 et 5. Coût API quotidien ≈ 1 000 appels (capitalisation) + rafraîchissement
hebdomadaire des états financiers. Actualités et résumés IA limités aux sociétés suivies (quota et coût LLM).

**U2 — Un seul lac de données.** Le modèle ML lit le même lac que l'application (plus de lac séparé) : une
société ingérée une fois sert aux deux. `bourse ml merge-lake` reprend l'ancien lac `data/ml` sans appel API.
Pairs par défaut des sociétés hors groupes configurés : même industrie (sinon même secteur), capitalisations
les plus proches.

**D-DBX1 — Même code, configuration par environnement.** `BOURSE_ENV=databricks` superpose
`config/settings.databricks.yaml` (registre MLflow Unity Catalog, expérience `/Shared/bourse_pfcf`).
Données sur un Volume Unity Catalog via le montage `/Volumes` ; les écritures retombent sur une copie si le
renommage atomique n'est pas supporté. Horodatages Parquet en microsecondes (lisibles par Spark) pour publier
les tables gold en Delta.

**D-DBX2 — Jobs = notebooks fins** qui appellent la CLI (`run_cli("run")`, `run_cli("ml", "train")`…) :
aucune logique propre à Databricks dans les traitements. Secrets lus dans le scope `bourse`.

**D-DBX3 — Application** : Databricks Apps n'a pas le montage `/Volumes` ; l'app copie les tables du Volume
sur son disque local (API Files, incrémental, toutes les 30 min) et charge le modèle depuis Unity Catalog.
