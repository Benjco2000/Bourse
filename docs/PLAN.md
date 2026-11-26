# Plan d'implémentation

Référence : spécification produit (5 pages + pipeline ML). Avancement par lots, chacun livré testé.

## Phase 1 — MVP : 10 sociétés suivies via FMP, toutes les pipelines en place

| Lot | Contenu | Statut |
|---|---|---|
| 1 | Socle : audit, univers MVP (3 groupes de pairs), couche `core`, seuils KPI centralisés, docs | ✅ |
| 2 | Modèle de données normalisé (indépendant de FMP), dates de publication, TTM point-in-time, capitalisation quotidienne, segments, pairs, contrôles de cohérence | ✅ |
| 3 | Calculs financiers testés : 7 KPIs, dette nette, ROCE, multiples de capitalisation, conversion multiple → prix | ✅ |
| 4 | Page 1 — Vue globale : recherche, fiche société, résumé IA du modèle économique, segments, 7 cartes KPI, concurrents modifiables, bandeau qualité | ✅ |
| 5 | Page 2 — Historique de valorisation : 5 multiples quotidiens point-in-time, médiane / quartiles | ✅ |
| 6 | Page 3 — Actualités & sentiment : ingestion news paginée, déduplication, score par article (LLM, repli lexical), synthèse LLM, historique | ✅ |
| 7 | Page 5 — De l'OCF au cash actionnaire : cascade, SBC, rachats, dividendes, acquisitions | ✅ |
| 8 | Pipeline ML : univers US (screener), lac ML, dataset point-in-time, baselines, Ridge / RF / HistGB (avec / sans segments, par secteur), validation chronologique + sociétés jamais vues, MLflow + registre (champion / retour arrière), prédictions batch, page 4 | ✅ |
| 9 | Durcissement : tests de bout en bout, UX, bandeau qualité, dépannage | à faire |
| 10 | Préparation production : `docs/ROADMAP_PRODUCTION.md` (FMP Starter/Premium, GCP vs Databricks, coûts) | ✅ (première version) |

Les fonctionnalités existantes (critères de Graham, screener, watchlist) sont conservées dans une section
« Bonus » de la navigation.

Compléments réalisés après le lot 8 :

| Lot | Contenu | Statut |
|---|---|---|
| 8 bis | Univers élargi dans l'application (~1 000 sociétés, jeu de données réduit), lac de données unique app + ML | ✅ |
| 10 bis | Déploiement Databricks Free Edition : notebooks de jobs, bundle, app, registre Unity Catalog (`docs/DEPLOY_DATABRICKS.md`) | ✅ préparé, non testé sur un workspace réel |

## Phase 2 — Extension de l'univers

Élargir l'ingestion (S&P 500 puis davantage), planifier les mises à jour, entraîner le modèle ML sur un volume
suffisant. Voir les limites ci-dessous.

## Historique disponible et IA

La grille FMP annonce 5 ans d'historique pour Starter, mais le premier run réel (19/11/2026) renvoie des
comptes annuels depuis **2011** (15 exercices demandés, 15 reçus). Les limites ont été relevées
(20 exercices, 40 trimestres, cours depuis 2008) pour mesurer la profondeur réellement disponible.

Un KPI « sur 5 ans » à une date passée exige 5 ans d'historique *avant* cette date : avec ~15 ans, on obtient
~10 ans de dates d'observation par société. Le facteur limitant de la phase 1 devient donc le **nombre de
sociétés** (10) : le pipeline ML sera exécutable, mais le modèle restera signalé comme non exploitable
statistiquement tant que l'univers ne comptera pas plusieurs centaines de sociétés (phase 2).
