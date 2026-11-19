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
l'ouverture d'une page) par un LLM (API Anthropic, modèle configurable), uniquement à partir de la
description FMP et des segments publiés, avec consigne de ne rien inventer. Mis en cache par empreinte des
entrées : il n'est régénéré que si la description, les segments ou le modèle changent. Affiché avec un
badge « Généré par IA », séparé de la description sourcée. Sans clé `ANTHROPIC_API_KEY`, rien n'est généré.

**P5 — Concurrents.** Par défaut, le groupe de pairs de la configuration (3 au maximum) ; l'utilisateur
peut les modifier, le choix est enregistré par société (`data/app/peer_overrides.json`) et réversible.

**P6 — Cartes KPI.** Statut porté par la couleur **et** une icône + libellé (accessibilité). Mini-courbe
de la série annuelle sous-jacente (10 derniers exercices), seuil et période toujours affichés.
