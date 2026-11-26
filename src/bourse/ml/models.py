"""Définition des modèles candidats (pipelines scikit-learn) et de la baseline."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from .dataset import CAT_FEATURES, KPI_FEATURES


class Winsorizer(BaseEstimator, TransformerMixin):
    """Borne chaque variable aux quantiles 1 % / 99 % appris sur l'entraînement (robustesse aux extrêmes)."""

    def __init__(self, lower: float = 0.01, upper: float = 0.99):
        self.lower = lower
        self.upper = upper

    def fit(self, X, y=None):
        X = pd.DataFrame(X)
        self.lo_ = X.quantile(self.lower).to_numpy()
        self.hi_ = X.quantile(self.upper).to_numpy()
        return self

    def transform(self, X):
        return np.clip(pd.DataFrame(X).to_numpy(dtype=float), self.lo_, self.hi_)

    def get_feature_names_out(self, input_features=None):
        return np.asarray(input_features) if input_features is not None else None


class GroupMedianBaseline(BaseEstimator, RegressorMixin):
    """Baseline : médiane de la cible par (secteur, tranche de capitalisation), repli secteur puis global."""

    def fit(self, X, y):
        df = pd.DataFrame(X).assign(_y=np.asarray(y))
        self.global_ = float(df["_y"].median())
        self.by_sector_ = df.groupby("sector")["_y"].median().to_dict()
        g = df.groupby(["sector", "cap_bucket"])["_y"].agg(["median", "count"])
        self.by_group_ = g[g["count"] >= 20]["median"].to_dict()
        return self

    def predict(self, X):
        df = pd.DataFrame(X)
        out = []
        for sector, bucket in zip(df["sector"], df["cap_bucket"], strict=True):
            out.append(self.by_group_.get((sector, bucket), self.by_sector_.get(sector, self.global_)))
        return np.asarray(out, dtype=float)


# Contraintes de monotonie (règles économiques) : +1 = le multiple « juste » ne peut que monter quand le KPI
# monte, -1 = ne peut que baisser, 0 = libre. Modifiables dans config/settings.yaml (ml.monotonic_constraints).
DEFAULT_MONOTONIC = {
    "revenue_cagr_5y": 1,     # croissance du CA : qualité ↑
    "fcf_cagr_5y": 1,         # croissance du FCF : qualité ↑
    "share_change_5y": -1,    # dilution : qualité ↓
    "roce_avg_5y": 1,         # rentabilité du capital : qualité ↑
    "net_debt_to_fcf": -1,    # endettement : qualité ↓
    "gross_margin_ttm": 1,    # pouvoir de prix : qualité ↑
    "fcf_margin_ttm": 1,      # conversion en cash : qualité ↑
}


def constraint_vector(constraints: dict[str, int] | None) -> list[int]:
    c = DEFAULT_MONOTONIC | (constraints or {})
    return [int(np.sign(c.get(f, 0))) for f in KPI_FEATURES]


def _preprocess(with_segments: bool, scale: bool, ordinal: bool, indicator: bool = True) -> ColumnTransformer:
    num = Pipeline([("winsor", Winsorizer()),
                    ("impute", SimpleImputer(strategy="median", add_indicator=indicator))]
                   + ([("scale", StandardScaler())] if scale else []))
    transformers = [("num", num, KPI_FEATURES)]
    if with_segments:
        cat = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1) \
            if ordinal else OneHotEncoder(handle_unknown="ignore", min_frequency=20)
        transformers.append(("cat", cat, CAT_FEATURES))
    return ColumnTransformer(transformers, remainder="drop")


class SignConstrainedRidge(BaseEstimator, RegressorMixin):
    """Ridge dont le signe des coefficients des KPIs est imposé (les autres colonnes restent libres).

    Astuce : chaque colonne contrainte est multipliée par son signe, chaque colonne libre est dupliquée en
    (x, -x), puis on ajuste une Ridge à coefficients positifs."""

    def __init__(self, signs: list[int] | None = None, alpha: float = 3.0):
        self.signs = signs
        self.alpha = alpha

    def _expand(self, X):
        X = np.asarray(X.toarray() if hasattr(X, "toarray") else X, dtype=float)
        signs = list(self.signs or []) + [0] * (X.shape[1] - len(self.signs or []))
        cols = []
        for j, sgn in enumerate(signs):
            if sgn:
                cols.append(sgn * X[:, j])
            else:
                cols += [X[:, j], -X[:, j]]
        return np.column_stack(cols)

    def fit(self, X, y):
        self.model_ = Ridge(alpha=self.alpha, positive=True).fit(self._expand(X), y)
        return self

    def predict(self, X):
        return self.model_.predict(self._expand(X))


def make_candidates(with_segments: bool, constraints: dict[str, int] | None = None) -> dict[str, BaseEstimator]:
    suffix = "+segments" if with_segments else ""
    mono = constraint_vector(constraints)
    cats = [0] * len(CAT_FEATURES) if with_segments else []
    hgb_pre = ColumnTransformer(
        [("num", Winsorizer(), KPI_FEATURES)]
        + ([("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1,
                                   encoded_missing_value=-1), CAT_FEATURES)] if with_segments else []))
    cat_mask = [False] * len(KPI_FEATURES) + ([True] * len(CAT_FEATURES) if with_segments else [])
    return {
        f"ridge{suffix}": Pipeline([("pre", _preprocess(with_segments, scale=True, ordinal=False)),
                                    ("model", SignConstrainedRidge(signs=mono, alpha=3.0))]),
        f"random_forest{suffix}": Pipeline([
            ("pre", _preprocess(with_segments, scale=False, ordinal=True, indicator=False)),
            ("model", RandomForestRegressor(n_estimators=300, min_samples_leaf=20, max_features=0.6, n_jobs=-1,
                                            random_state=42, monotonic_cst=mono + cats))]),
        f"hist_gradient_boosting{suffix}": Pipeline([
            ("pre", hgb_pre),
            ("model", HistGradientBoostingRegressor(categorical_features=cat_mask if with_segments else None,
                                                    monotonic_cst=mono + cats, max_iter=400, learning_rate=0.05,
                                                    min_samples_leaf=40, l2_regularization=1.0,
                                                    random_state=42))]),
    }


COMPLEXITY = {"baseline": 0, "ridge": 1, "random_forest": 2, "hist_gradient_boosting": 3, "per_sector": 4}


def complexity(name: str) -> int:
    return COMPLEXITY.get(name.split("+")[0].split(":")[0], 5) + (0.5 if "+segments" in name else 0)


class PerSectorModel(BaseEstimator, RegressorMixin):
    """Un modèle par secteur quand le volume le permet, sinon le modèle global (avec segments)."""

    def __init__(self, base_factory=None, min_obs: int = 1500, constraints: dict[str, int] | None = None):
        self.base_factory = base_factory
        self.min_obs = min_obs
        self.constraints = constraints

    def fit(self, X, y):
        X = pd.DataFrame(X)
        y = np.asarray(y)
        factory = self.base_factory or (
            lambda: make_candidates(True, self.constraints)["hist_gradient_boosting+segments"])
        self.global_ = factory().fit(X, y)
        self.sector_models_ = {}
        for sector, idx in X.groupby("sector").groups.items():
            if len(idx) >= self.min_obs:
                mask = X.index.isin(idx)
                self.sector_models_[sector] = factory().fit(X[mask], y[mask])
        return self

    def predict(self, X):
        X = pd.DataFrame(X)
        pred = self.global_.predict(X)
        for sector, model in self.sector_models_.items():
            mask = (X["sector"] == sector).to_numpy()
            if mask.any():
                pred[mask] = model.predict(X[mask])
        return pred
