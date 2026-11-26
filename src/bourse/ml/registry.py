"""Registre de modèles (MLflow) : configuration, enregistrement, champion, retour arrière, `load_model()`.

Local : base sqlite `data/ml/mlflow.db` + artefacts dans `data/ml/mlartifacts`.
Phase 2 (Databricks) : `ml.registry.tracking_uri: databricks` et `registry_uri: databricks-uc`, nom de modèle
`<catalog>.<schema>.bourse_pfcf` — le reste du code est inchangé.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
log = logging.getLogger(__name__)

CHAMPION = "champion"
META_ARTIFACT = "bundle_meta.json"


def configure(settings: Settings) -> str:
    import mlflow

    reg = settings.section("ml").get("registry", {})
    base = settings.data_dir / settings.section("ml").get("data_subdir", "ml")
    base.mkdir(parents=True, exist_ok=True)
    tracking = reg.get("tracking_uri") or f"sqlite:///{(base / 'mlflow.db').as_posix()}"
    mlflow.set_tracking_uri(tracking)
    mlflow.set_registry_uri(reg.get("registry_uri") or tracking)
    return tracking


def registry_exists(settings: Settings) -> bool:
    """Vrai si un registre distant est configuré ou si la base locale existe (évite de la créer pour rien)."""
    reg = settings.section("ml").get("registry", {})
    base = settings.data_dir / settings.section("ml").get("data_subdir", "ml")
    return bool(reg.get("tracking_uri")) or (base / "mlflow.db").exists()


def experiment(settings: Settings, name: str = "bourse_pfcf") -> str:
    import mlflow

    base = settings.data_dir / settings.section("ml").get("data_subdir", "ml")
    exp = mlflow.get_experiment_by_name(name)
    if exp is None:
        location = None if str(mlflow.get_tracking_uri()).startswith("databricks") else \
            (base / "mlartifacts").resolve().as_uri()
        return mlflow.create_experiment(name, artifact_location=location)
    return exp.experiment_id


def model_name(settings: Settings) -> str:
    return settings.section("ml").get("registry", {}).get("model_name", "bourse_pfcf")


@dataclass
class ModelBundle:
    model: Any
    meta: dict[str, Any]
    version: str

    @property
    def usable(self) -> bool:
        return bool(self.meta.get("usability", {}).get("usable"))


def load_model(settings: Settings, alias: str = CHAMPION, version: str | None = None) -> ModelBundle | None:
    """Charge le modèle (par alias, par défaut « champion », ou par version) et ses métadonnées.
    Retourne None si aucun modèle n'est enregistré."""
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except ImportError:
        log.warning("mlflow non installé : pip install -e \".[ml]\"")
        return None
    if not registry_exists(settings):
        return None
    configure(settings)
    client = MlflowClient()
    name = model_name(settings)
    try:
        mv = client.get_model_version(name, version) if version else client.get_model_version_by_alias(name, alias)
    except Exception:
        return None
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = mlflow.sklearn.load_model(f"models:/{name}/{mv.version}")
    local = mlflow.artifacts.download_artifacts(run_id=mv.run_id, artifact_path=META_ARTIFACT)
    meta = json.loads(Path(local).read_text(encoding="utf-8"))
    return ModelBundle(model, meta, str(mv.version))


def list_versions(settings: Settings) -> list[dict[str, Any]]:
    from mlflow.tracking import MlflowClient

    configure(settings)
    client = MlflowClient()
    name = model_name(settings)
    try:
        versions = client.search_model_versions(f"name='{name}'")
        champion = client.get_model_version_by_alias(name, CHAMPION).version
    except Exception:
        return []
    out = []
    for v in sorted(versions, key=lambda v: int(v.version)):
        run = client.get_run(v.run_id)
        out.append({"version": str(v.version), "champion": str(v.version) == str(champion), "run_id": v.run_id,
                    "model": run.data.params.get("selected_model"), "created": v.creation_timestamp,
                    "test_medae": run.data.metrics.get("test_medae"), "usable": v.tags.get("usable")})
    return out


def promote(settings: Settings, version: str) -> None:
    """Désigne une version comme champion (permet de revenir à un modèle précédent)."""
    from mlflow.tracking import MlflowClient

    configure(settings)
    MlflowClient().set_registered_model_alias(model_name(settings), CHAMPION, str(version))
    log.info("Version %s du modèle %s désignée champion", version, model_name(settings))
