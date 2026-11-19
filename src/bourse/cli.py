"""Ligne de commande : `bourse <commande>` (ou `python -m bourse <commande>`).

    bourse run                 # pipeline complet : ingestion FMP -> silver -> gold -> contrôles qualité
    bourse run --force         # ignore les fréquences de rafraîchissement (tout re-télécharger)
    bourse ingest --tickers MSFT AAPL --datasets quote prices_daily
    bourse transform           # silver + gold + qualité, sans appel API
    bourse transform --full-refresh
    bourse demo                # pipeline complet avec des données SYNTHÉTIQUES (sans clé API) dans data_demo/
    bourse status              # état des derniers runs et de la qualité
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

    p_tr = sub.add_parser("transform", help="silver + gold + qualité (aucun appel API)")
    p_tr.add_argument("--full-refresh", action="store_true")

    p_demo = sub.add_parser("demo", help="données synthétiques, sans clé API")
    p_demo.add_argument("--data-dir", default=None, help="dossier cible (défaut : data_demo/)")

    sub.add_parser("status", help="derniers runs & contrôles qualité")
    sub.add_parser("datasets", help="liste des jeux de données")

    args = parser.parse_args(argv)
    setup_logging()
    settings = get_settings()

    if args.command == "datasets":
        for d in DATASETS:
            print(f"{d.name:28s} {d.scope:7s} {d.frequency:7s} /stable/{d.path}")
        return 0

    if args.command == "demo":
        from .fmp.fake import FakeFMPClient

        data_dir = Path(args.data_dir) if args.data_dir else settings.data_dir.parent / "data_demo"
        settings.data_dir = data_dir
        store = Store(data_dir)
        result = run_pipeline(settings, store, client=FakeFMPClient(), force=True, demo=True)
        print(f"\nDonnées de démonstration écrites dans {data_dir}")
        print(f"Lancez l'app : BOURSE_DATA_DIR={data_dir} streamlit run app/streamlit_app.py")
        return 0 if result["status"] != "failed" else 1

    store = Store(settings.data_dir)

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
        result = run_pipeline(settings, store, steps=("ingest", "silver"), tickers=args.tickers,
                              datasets=args.datasets, force=args.force)
    else:  # transform
        result = run_pipeline(settings, store, steps=("silver", "gold", "quality"),
                              full_refresh=args.full_refresh)

    if os.getenv("BOURSE_LOG_LEVEL", "INFO").upper() == "DEBUG":
        print(json.dumps(result, indent=2, default=str))
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
