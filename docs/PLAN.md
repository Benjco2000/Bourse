# Plan d'implémentation

Référence : spécification produit (5 pages + pipeline ML). Avancement par lots, chacun livré testé.

## Phase 1 — MVP : 10 sociétés via FMP, toutes les pipelines en place

| Lot | Contenu | Statut |
|---|---|---|
| 1 | Socle : audit, univers MVP (3 groupes de pairs), couche `core`, seuils KPI centralisés, docs | ✅ |
| 2 | Modèle de données normalisé (indépendant de FMP), dates de publication, TTM point-in-time, capitalisation quotidienne, segments, pairs, contrôles de cohérence | ✅ |
| 3 | Calculs financiers testés : 7 KPIs, dette nette, ROCE, multiples de capitalisation, conversion multiple → prix | ✅ |
| 4 | Page 1 — Vue globale : recherche, fiche société, segments, 7 cartes KPI, tableau concurrents | à faire |
| 5 | Page 2 — Historique de valorisation : 5 multiples quotidiens point-in-time, médiane / quartiles | à faire |
| 6 | Page 3 — Actualités & sentiment : ingestion news, déduplication, synthèse LLM (repli lexical), historique | à faire |
| 7 | Page 5 — De l'OCF au cash actionnaire : cascade, SBC, rachats, dividendes, acquisitions | à faire |
| 8 | Pipeline ML : dataset temporel, baselines, Ridge / RF / HistGB, validation chronologique, MLflow, `load_model()`, page 4 | à faire |
| 9 | Durcissement : tests de bout en bout, UX, bandeau qualité, dépannage | à faire |
| 10 | Préparation production : `docs/ROADMAP_PRODUCTION.md` (FMP Starter/Premium, GCP vs Databricks, coûts) | à faire |

Les fonctionnalités existantes (critères de Graham, screener, watchlist) sont conservées dans une section
« Bonus » de la navigation.

## Phase 2 — Extension de l'univers

Élargir l'ingestion (S&P 500 puis davantage), planifier les mises à jour, entraîner le modèle ML sur un volume
suffisant. Voir les limites ci-dessous.

## Point bloquant identifié pour l'IA

D'après la grille FMP, l'historique est de **5 ans** pour les plans Basic et Starter, **30+ ans** à partir de
Premium. Un KPI « sur 5 ans » à une date passée exige 5 ans d'historique *avant* cette date : avec 5 ans au
total, on n'obtient qu'une ou deux dates d'observation par société. Le pipeline ML sera construit et exécutable
en phase 1, mais le modèle ne sera **pas statistiquement exploitable** sans Premium (ou une autre source
d'historique long) et un univers de plusieurs centaines de sociétés. Cela sera affiché dans l'application.
