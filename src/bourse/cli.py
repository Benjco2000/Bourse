"""Ligne de commande : `bourse <commande>` (ou `python -m bourse <commande>`).

    bourse run                 # pipeline complet : ingestion FMP -> silver -> gold -> contrôles qualité
    bourse run --force         # ignore les fréquences de rafraîchissement (tout re-télécharger)
    bourse ingest --tickers MSFT AAPL --datasets quote prices_daily
    bourse transform           # silver + gold + qualité, sans appel API
    bourse transform --full-refresh
    bourse demo                # pipeline complet avec des données SYNTHÉTIQUES (sans clé API) dans data_demo/
    bourse status              # état des derniers runs et de la qualité
    bourse anomalies [MSFT]    # détail des incohérences détectées dans les états financiers
    bourse ml all              # valorisation IA : univers -> ingestion -> dataset -> entraînement -> prédictions
    bourse ml models           # versions du modèle ; `bourse ml promote 3` pour revenir à la v3
    bourse probe AAPL          # diagnostic : quels endpoints votre plan FMP autorise
    bourse datasets            # liste des jeux de données FMP ingérés
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import get_settings, setup_logging
from .fmp.datasets import DATASETS
from .pipeline import run_pipeline
from .storage import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bourse", description="Pipeline de données boursières FMP")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--tickers", nargs="+", help="restreindre à ces tickers")
        p.add_argument("--datasets", nargs="+", choices=[d.name for d in DATASETS],
                       help="restreindre à ces jeux de données")

    p_run = sub.add_parser("run", help="pipeline complet")
    common(p_run)
    p_run.add_argument("--force", action="store_true", help="ignorer la politique de fraîcheur")
    p_run.add_argument("--full-refresh", action="store_true", help="reconstruire silver depuis tout le bronze")

    p_ing = sub.add_parser("ingest", help="ingestion API -> bronze -> silver uniquement")
    common(p_ing)
    p_ing.add_argument("--force", action="store_true")

    p_tr = sub.add_parser("transform", help="silver + core + gold + qualité (aucun appel API)")
    p_tr.add_argument("--full-refresh", action="store_true")

    p_demo = sub.add_parser("demo", help="données synthétiques, sans clé API")
    p_demo.add_argument("--data-dir", default=None, help="dossier cible (défaut : data_demo/)")

    sub.add_parser("status", help="derniers runs & contrôles qualité")
    p_ml = sub.add_parser("ml", help="valorisation IA : univers, ingestion, dataset, entraînement, prédictions")
    ml_sub = p_ml.add_subparsers(dest="ml_command", required=True)
    ml_sub.add_parser("universe", help="construit config/universe_ml.csv via le screener FMP")
    p_mi = ml_sub.add_parser("ingest", help="ingestion des données d'entraînement (lac data/ml)")
    p_mi.add_argument("--limit", type=int, help="n'ingérer que les N premières sociétés (test)")
    p_mi.add_argument("--force", action="store_true")
    ml_sub.add_parser("dataset", help="construit le dataset point-in-time")
    p_mt = ml_sub.add_parser("train", help="entraîne, évalue, enregistre le modèle (MLflow)")
    p_mt.add_argument("--promote", action=argparse.BooleanOptionalAction, default=None,
                      help="forcer (--promote) ou empêcher (--no-promote) la désignation comme champion")
    ml_sub.add_parser("predict", help="applique le champion aux sociétés suivies (gold.ml_predictions)")
    ml_sub.add_parser("models", help="liste les versions enregistrées")
    p_mp = ml_sub.add_parser("promote", help="désigne une version comme champion (retour arrière)")
    p_mp.add_argument("version")
    p_ma = ml_sub.add_parser("all", help="universe (si absent) + ingest + dataset + train + predict")
    p_ma.add_argument("--limit", type=int)
    ml_sub.add_parser("ui", help="commande pour ouvrir l'interface MLflow")
    ml_sub.add_parser("merge-lake", help="fusionne l'ancien lac ML séparé (data/ml) dans le lac principal")
    p_ano = sub.add_parser("anomalies", help="détail des anomalies détectées dans les états financiers")
    p_ano.add_argument("tickers", nargs="*")
    p_ano.add_argument("--all", action="store_true", help="inclure les signalements informatifs")
    p_probe = sub.add_parser("probe", help="teste chaque endpoint FMP pour un ticker (diagnostic de plan)")
    p_probe.add_argument("ticker", nargs="?", default="AAPL")
    sub.add_parser("datasets", help="liste des jeux de données")

    args = parser.parse_args(argv)
    setup_logging()
    settings = get_settings()

    if args.command == "datasets":
        for d in DATASETS:
            print(f"{d.name:28s} {d.scope:7s} {d.frequency:7s} /stable/{d.path}")
        return 0

    if args.command == "ml":
        return _ml(settings, args)

    if args.command == "demo":
        from .fmp.fake import FakeFMPClient

        data_dir = Path(args.data_dir) if args.data_dir else settings.data_dir.parent / "data_demo"
        settings.data_dir = data_dir
        store = Store(data_dir)
        result = run_pipeline(settings, store, client=FakeFMPClient(), force=True, demo=True)
        _ml_demo(settings, store)
        print(f"\nDonnées de démonstration écrites dans {data_dir}")
        print(f"Lancez l'app : BOURSE_DATA_DIR={data_dir} streamlit run app/streamlit_app.py")
        return 0 if result["status"] != "failed" else 1

    if args.command == "probe":
        return _probe(settings, args.ticker.upper())

    store = Store(settings.data_dir)

    if args.command == "anomalies":
        import pandas as pd

        st = store.read("core", "financial_statements")
        if st.empty:
            print("Aucun état financier en base.")
            return 0
        info = set() if args.all else {"availability_estimated", "cost_of_revenue_zero"}
        st = st[st["quality_flags"].fillna("").map(lambda f: any(x and x not in info for x in f.split(",")))]
        if args.tickers:
            st = st[st["ticker"].isin([t.upper() for t in args.tickers])]
        cols = ["ticker", "period_type", "period_end", "quality_flags", "revenue", "cost_of_revenue", "gross_profit",
                "total_assets", "total_liabilities", "total_equity", "total_equity_incl_minority",
                "minority_interest", "operating_cash_flow"]
        with pd.option_context("display.width", 250, "display.max_columns", 20, "display.float_format",
                               lambda x: f"{x / 1e9:,.2f}Md"):
            print(st[[c for c in cols if c in st.columns]].to_string(index=False) if not st.empty
                  else "Aucune anomalie.")
        return 0

    if args.command == "status":
        runs = store.read("ops", "pipeline_runs")
        if runs.empty:
            print("Aucun run enregistré.")
            return 0
        print(runs.sort_values("started_at").tail(10)[["run_id", "started_at", "duration_s", "status", "steps"]]
              .to_string(index=False))
        qa = store.read("ops", "quality_report")
        if not qa.empty:
            print("\nContrôles qualité :")
            print(qa[["check", "status", "detail"]].to_string(index=False))
        return 0

    if args.command in ("run", "ingest") and not settings.api_key:
        print("Erreur : FMP_API_KEY introuvable. Copiez .env.example en .env et renseignez votre clé.",
              file=sys.stderr)
        return 2

    if args.command == "run":
        result = run_pipeline(settings, store, tickers=args.tickers, datasets=args.datasets, force=args.force,
                              full_refresh=args.full_refresh)
    elif args.command == "ingest":
        result = run_pipeline(settings, store, steps=("ingest", "silver", "core"), tickers=args.tickers,
                              datasets=args.datasets, force=args.force)
    else:  # transform
        result = run_pipeline(settings, store, steps=("silver", "core", "gold", "quality"),
                              full_refresh=args.full_refresh)

    if os.getenv("BOURSE_LOG_LEVEL", "INFO").upper() == "DEBUG":
        print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] != "failed" else 1


def _ml_demo(settings, store: Store) -> None:
    """Pipeline ML complet sur données synthétiques (univers fictif de 70 sociétés)."""
    import copy
    import dataclasses

    try:
        from .fmp.fake import FakeFMPClient
        from .ml.dataset import write_dataset
        from .ml.lake import ingest_ml, ml_store
        from .ml.train import train
        from .ml.universe import build_universe
    except ImportError:
        print("mlflow / scikit-learn non installés : démo ML ignorée (pip install -e \".[ml]\").")
        return
    raw = copy.deepcopy(settings.raw)
    universe_file = str(settings.data_dir / "ml" / "universe_ml.csv")
    raw["ml"]["universe_file"] = universe_file
    raw["universe"]["extended_universe_file"] = universe_file
    s = dataclasses.replace(settings, raw=raw)
    client = FakeFMPClient()
    build_universe(s, client)
    ingest_ml(s, client, force=True)
    write_dataset(s, ml_store(s))
    train(s, ml_store(s), promote=True)
    run_pipeline(s, store, steps=("gold", "quality"), demo=True)  # univers élargi + prédictions



def _ml(settings, args) -> int:
    from .fmp.client import FMPError
    from .pipeline import make_client

    try:
        from .ml import registry
        from .ml.dataset import write_dataset
        from .ml.lake import ingest_ml, merge_legacy_lake, ml_store
        from .ml.predict import run_predictions
        from .ml.train import train
        from .ml.universe import build_universe, universe_path
    except ImportError as exc:
        print(f"Dépendances ML manquantes ({exc}) : pip install -e \".[ml]\"", file=sys.stderr)
        return 2
    cmd = args.ml_command
    needs_api = cmd in ("universe", "ingest") or (cmd == "all")
    if needs_api and not settings.api_key:
        print("Erreur : FMP_API_KEY introuvable (.env).", file=sys.stderr)
        return 2
    try:
        if cmd in ("universe",) or (cmd == "all" and not universe_path(settings).exists()):
            df = build_universe(settings, make_client(settings))
            print(f"{len(df)} sociétés dans {universe_path(settings)}")
        if cmd in ("ingest", "all"):
            ingest_ml(settings, make_client(settings), limit=args.limit, force=getattr(args, "force", False))
            # indicateurs de l'application pour l'univers élargi (KPIs, multiples, cascade de trésorerie...)
            run_pipeline(settings, Store(settings.data_dir), steps=("gold", "quality"))
        if cmd == "merge-lake":
            res = merge_legacy_lake(settings)
            print(f"{res['files']} fichiers bruts récupérés depuis data/ml : reconstruction des tables…")
            run_pipeline(settings, Store(settings.data_dir), steps=("silver", "core", "gold", "quality"),
                         full_refresh=True)
        if cmd in ("dataset", "all"):
            _, card = write_dataset(settings, ml_store(settings))
            print(json.dumps({k: card[k] for k in ("n_rows", "n_companies", "period", "exclusions")},
                             ensure_ascii=False, indent=2, default=str))
        if cmd in ("train", "all"):
            res = train(settings, ml_store(settings), promote=getattr(args, "promote", None))
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        if cmd in ("predict", "all"):
            bundle = registry.load_model(settings)
            if bundle is None:
                print("Aucun modèle champion : lancez `bourse ml train`.", file=sys.stderr)
                return 1
            run_predictions(settings, Store(settings.data_dir), bundle)
        if cmd == "models":
            versions = registry.list_versions(settings)
            print("\n".join(f"v{v['version']:>3} {'★ champion' if v['champion'] else '          '} "
                            f"{v['model']:<36} test MedAE {v['test_medae'] or float('nan'):6.2f}  "
                            f"exploitable={v['usable']}" for v in versions) or "Aucun modèle enregistré.")
        if cmd == "promote":
            registry.promote(settings, args.version)
        if cmd == "ui":
            uri = registry.configure(settings)
            print(f"mlflow ui --backend-store-uri {uri}")
    except (FMPError, FileNotFoundError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


def _probe(settings, ticker: str) -> int:
    """Appelle une fois chaque endpoint et affiche le résultat (≈ 26 appels API)."""
    from .fmp.client import FMPError, PlanRestrictedError
    from .pipeline import make_client

    if not settings.api_key:
        print("Erreur : FMP_API_KEY introuvable.", file=sys.stderr)
        return 2
    client = make_client(settings)
    cfg = settings.raw | {"history": settings.history}
    print(f"Plan configuré : {settings.plan} — ticker testé : {ticker}\n")
    for ds in DATASETS:
        params = dict(ds.params(cfg))
        if ds.scope == "symbol":
            params[ds.symbol_param] = ticker
        try:
            payload = client.get(ds.path, params)
            n = len(payload) if isinstance(payload, list) else 1
            status = f"OK ({n} lignes)" if n else "OK (vide)"
        except PlanRestrictedError as exc:
            status = f"REFUSÉ — {str(exc)[:110]}"
        except FMPError as exc:
            status = f"ERREUR — {str(exc)[:110]}"
        print(f"{ds.name:28s} {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
