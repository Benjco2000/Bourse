.PHONY: install run transform demo app app-demo status test lint

install:            ## Installe le projet et les outils de dev dans l'environnement courant
	pip install -e ".[dev]"

run:                ## Pipeline complet (API FMP -> bronze -> silver -> gold -> qualité)
	bourse run

transform:          ## Recalcule silver/gold sans appeler l'API
	bourse transform

demo:               ## Données synthétiques (sans clé) dans data_demo/
	bourse demo

app:                ## Lance l'application sur les vraies données (data/)
	streamlit run app/streamlit_app.py

app-demo:           ## Lance l'application sur les données de démonstration
	BOURSE_DATA_DIR=./data_demo streamlit run app/streamlit_app.py

status:             ## Derniers runs + contrôles qualité
	bourse status

test:
	pytest -q

lint:
	ruff check .
