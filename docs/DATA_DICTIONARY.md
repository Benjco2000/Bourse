# Dictionnaire de données — couche `core` et KPIs

Contrats indépendants du fournisseur. Montants en unités de la devise de publication (`reported_currency`),
sauf mention contraire. Conventions de signe : voir `DECISIONS.md` (F2).

## core.companies — une ligne par société

| Champ | Type | Description |
|---|---|---|
| ticker | str | Symbole boursier (clé) |
| name, exchange, exchange_full_name | str | Nom, place de cotation |
| sector, industry, country | str | Classification de la source (FMP) |
| trading_currency | str | Devise de cotation |
| description, website, ceo, employees, ipo_date, image, cik, isin | — | Informations générales (sourcées) |
| source, retrieved_at | str, datetime | Provenance et date de récupération |

## core.financial_statements — une ligne par (ticker, period_type, period_end)

| Champ | Description |
|---|---|
| ticker, period_end | Clé ; `period_end` = date de clôture de la période |
| period_type | `annual` ou `quarter` |
| period_label | `FY`, `Q1`…`Q4` (libellé de la source) |
| fiscal_year | Exercice fiscal réel (peut différer de l'année calendaire) |
| reported_currency, unit | Devise de publication ; `units` |
| filing_date, accepted_date | Dates de dépôt / d'acceptation (SEC) si fournies |
| available_date | Date à partir de laquelle l'information est utilisable (cf. T1) |
| availability_estimated | True si `available_date` est estimée (délai standard) |
| revenue, cost_of_revenue, gross_profit | CA, coût des ventes, marge brute (= CA − coût des ventes) |
| operating_income, ebit, pretax_income, income_tax, net_income, eps_diluted | Compte de résultat |
| shares_basic, shares_diluted | Nombre moyen pondéré d'actions |
| total_assets, current_assets, current_liabilities, total_liabilities, total_equity | Bilan |
| cash_and_equivalents, short_term_investments | Trésorerie et placements court terme |
| total_debt, short_term_debt, long_term_debt | Dette financière |
| operating_cash_flow | Flux de trésorerie d'exploitation (OCF) |
| capex | Investissements corporels, **montant positif** |
| free_cash_flow | OCF − capex |
| stock_based_compensation | Rémunération en actions (charge non cash, réintégrée dans l'OCF) |
| dividends_paid, share_repurchases, stock_issuance | Montants positifs |
| acquisitions | Cash dépensé en acquisitions, net des cessions (positif = dépense) |
| net_debt_issuance, net_change_in_cash | Signe publié (positif = entrée) |
| quality_flags | Anomalies détectées, séparées par des virgules (voir ci-dessous) |
| source, retrieved_at | Provenance |

`quality_flags` : `revenue_missing`, `revenue_non_positive`, `cost_of_revenue_zero`, `gross_profit_mismatch`,
`balance_sheet_mismatch`, `shares_missing`, `ocf_missing`, `availability_estimated`.

## core.ttm — agrégats 12 mois glissants, point-in-time

| Champ | Description |
|---|---|
| ticker, period_end | Fin de la dernière période incluse |
| available_date | Date de disponibilité (celle de la période la plus récente) |
| method | `sum_4_quarters` (4 trimestres consécutifs) ou `annual` (repli : exercice annuel) |
| revenue … net_change_in_cash | Flux sommés sur 12 mois (mêmes définitions que ci-dessus) |
| total_assets … shares_diluted | Postes de bilan / actions de la dernière période |

## core.prices_daily

`ticker, date, close, volume, source` — cours de clôture quotidiens (non ajustés des dividendes).

## core.market_cap_daily

| Champ | Description |
|---|---|
| ticker, date | Clé |
| market_cap | Capitalisation boursière (devise de cotation) |
| source | `fmp_historical_market_cap` ou `approx_close_x_diluted_shares` (cf. T3) |

## core.revenue_segments (format long)

`ticker, fiscal_year, period_end, segment_type (product | geography), segment, revenue, source`.

## core.peers

`ticker, peer, source (config:<groupe> | fmp_stock_peers), peer_in_universe`.

## gold.kpis — 7 KPIs par société

| Champ | Description |
|---|---|
| ticker, kpi_id, label | Identifiant et libellé |
| value | Valeur (ratio décimal pour les %, années pour Dette nette / FCF), vide si N/A |
| unit | `percent` ou `years` |
| status | `good` (seuil atteint), `bad` (non atteint), `warning` (historique insuffisant / non interprétable / donnée douteuse), `na` (indisponible) |
| threshold, direction | Seuil configuré (`config/settings.yaml` → `kpis`) et sens (`higher` / `lower`) |
| period_label | Période utilisée (ex. `FY2020→FY2025`, `TTM au 09/2026`) |
| explanation, note | Définition ; raison d'un N/A ou d'un statut orange |
| history | Série annuelle `année:valeur;…` pour la mini-courbe |
| balance_date, computed_at | Date du bilan utilisé ; date de calcul |

| kpi_id | Définition | Seuil favorable |
|---|---|---|
| revenue_cagr_5y | TCAM du CA sur 5 exercices | > 10 % |
| fcf_cagr_5y | TCAM du FCF sur 5 exercices (départ et arrivée > 0) | > 10 % |
| share_change_5y | Variation cumulée des actions diluées sur 5 exercices | < 0 % |
| roce_avg_5y | Moyenne des ROCE annuels valides (5 derniers exercices) | > 15 % |
| net_debt_to_fcf | Dette nette (dernier bilan) / FCF TTM | < 3 ans |
| gross_margin_ttm | Marge brute TTM | > 15 % |
| fcf_margin_ttm | FCF TTM / CA TTM | > 15 % |

## gold.company_snapshot — situation actuelle (une ligne par société)

| Champ | Description |
|---|---|
| ticker, name, exchange, sector, industry, country | Identité |
| trading_currency, reported_currency | Devise de cotation / des comptes |
| market_cap, market_cap_date, market_cap_source | Dernière capitalisation connue et sa source |
| cap_bucket | `small` < 2 Md$, `mid` < 10 Md$, `large` < 100 Md$, sinon `mega` (configurable) |
| revenue_ttm, gross_profit_ttm, net_income_ttm, operating_cash_flow_ttm, capex_ttm, free_cash_flow_ttm, stock_based_compensation_ttm, shares_diluted_ttm | Agrégats 12 mois disponibles à `market_cap_date` |
| ttm_period_end, ttm_available_date, ttm_method | Période, disponibilité et méthode du TTM |
| ps, pocf, pfcf | Capitalisation / CA, OCF, FCF TTM (N/A si dénominateur ≤ 0 ou devises différentes) |
| ps_note, pocf_note, pfcf_note | Raison d'un N/A |
| quality_level, quality_notes | `complete` / `partial` / `degraded` ; explications séparées par « \| » |

## gold.business_summaries — résumés générés par IA

| Champ | Description |
|---|---|
| ticker | Société |
| summary_md | Texte Markdown (4 rubriques : produits et services, clients, modèle de revenus, segments) |
| model, generated_at | Modèle LLM et date de génération |
| input_hash | Empreinte des entrées (cache) |
| segments_fiscal_year | Exercice des segments utilisés |
