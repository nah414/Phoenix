"""
Dr. Frank & Eddy v6.0.0 -- Drift Ensemble Predictor

Ensemble of RF + GradientBoosting (CPU) + PhaseDriftTCN (CUDA).
Falls back to CPU-only ensemble if CUDA unavailable.

Usage:
    from ml.drift_ensemble import DriftEnsemble
    ensemble = DriftEnsemble()
    ensemble.train("C:/frank-data/drift/synthetic_train.npz")
    predictions = ensemble.predict(features)
"""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = "C:/frank-data/drift/models"

# Check optional dependencies
try:
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

try:
    import torch
    HAS_TORCH = torch.cuda.is_available()
except ImportError:
    HAS_TORCH = False


class DriftEnsemble:
    """
    Ensemble drift predictor.

    Weights: RF=0.3, GB=0.3, TCN=0.4 (when CUDA available).
    Falls back to RF=0.5, GB=0.5 when TCN unavailable.
    """

    def __init__(self, model_dir: str = DEFAULT_MODEL_DIR):
        self._model_dir = Path(model_dir)
        self._rf = None
        self._gb = None
        self._tcn = None
        self._tcn_available = False
        self._trained = False

    def train(self, data_path: str, epochs: int = 20) -> dict:
        """
        Train all ensemble models on .npz data.

        Args:
            data_path: Path to .npz with x (N,T,F) and y (N,) arrays.
            epochs: Training epochs for TCN (if available).

        Returns:
            Dict with training metrics.
        """
        data = np.load(data_path)
        x_raw = data["x"]  # (N, T, F)
        y = data["y"]       # (N,)

        # Flatten for sklearn: (N, T*F)
        n_samples = x_raw.shape[0]
        x_flat = x_raw.reshape(n_samples, -1)

        metrics = {}

        # Train RF
        if HAS_SKLEARN:
            self._rf = RandomForestRegressor(
                n_estimators=100, max_depth=10, n_jobs=-1, random_state=42
            )
            self._rf.fit(x_flat, y)
            rf_pred = self._rf.predict(x_flat)
            metrics["rf_train_mse"] = float(np.mean((rf_pred - y) ** 2))
            log.info("RF trained: MSE=%.6f", metrics["rf_train_mse"])

            # Train GB
            self._gb = GradientBoostingRegressor(
                n_estimators=100, max_depth=5, learning_rate=0.1, random_state=42
            )
            self._gb.fit(x_flat, y)
            gb_pred = self._gb.predict(x_flat)
            metrics["gb_train_mse"] = float(np.mean((gb_pred - y) ** 2))
            log.info("GB trained: MSE=%.6f", metrics["gb_train_mse"])

        # Train TCN (if torch + CUDA available)
        if HAS_TORCH:
            try:
                from ml.dcq_drift_model import PhaseDriftTCN
                n_features = x_raw.shape[2]
                self._tcn = PhaseDriftTCN(in_features=n_features)
                self._tcn = self._tcn.to("cuda")
                self._tcn.train()

                x_t = torch.tensor(x_raw, dtype=torch.float32).cuda()
                y_t = torch.tensor(y, dtype=torch.float32).unsqueeze(1).cuda()

                optimizer = torch.optim.Adam(self._tcn.parameters(), lr=1e-3)
                loss_fn = torch.nn.MSELoss()

                for epoch in range(epochs):
                    optimizer.zero_grad()
                    pred = self._tcn(x_t)
                    loss = loss_fn(pred, y_t)
                    loss.backward()
                    optimizer.step()

                self._tcn.eval()
                self._tcn_available = True
                metrics["tcn_final_loss"] = float(loss.item())
                log.info("TCN trained: loss=%.6f", metrics["tcn_final_loss"])
            except Exception as e:
                log.warning("TCN training failed, using CPU-only ensemble: %s", e)
                self._tcn_available = False
        else:
            # Fallback: try importing DCQ's numpy fallback TCN
            try:
                from ml.dcq_drift_model import PhaseDriftTCN as FallbackTCN
                n_features = x_raw.shape[2]
                self._tcn = FallbackTCN(in_features=n_features)
                # Numpy fallback doesn't train -- just linear regression
                self._tcn_available = False
                log.info("TCN fallback loaded (numpy, no GPU training)")
            except ImportError:
                log.info("No TCN available, using RF+GB only")

        self._trained = True
        metrics["ensemble_models"] = self._active_model_names()
        return metrics

    def predict(self, features: np.ndarray) -> np.ndarray:
        """
        Ensemble prediction.

        Args:
            features: (N, T, F) feature array.

        Returns:
            (N,) predictions.
        """
        if not self._trained:
            raise RuntimeError("Ensemble not trained. Call train() first.")

        n = features.shape[0]
        x_flat = features.reshape(n, -1)
        preds = []
        weights = []

        if self._rf is not None:
            preds.append(self._rf.predict(x_flat))
            weights.append(0.3 if self._tcn_available else 0.5)

        if self._gb is not None:
            preds.append(self._gb.predict(x_flat))
            weights.append(0.3 if self._tcn_available else 0.5)

        if self._tcn_available and self._tcn is not None:
            import torch
            with torch.no_grad():
                x_t = torch.tensor(features, dtype=torch.float32).cuda()
                tcn_pred = self._tcn(x_t).cpu().numpy().flatten()
            preds.append(tcn_pred)
            weights.append(0.4)

        if not preds:
            raise RuntimeError("No models available for prediction")

        # Weighted average
        weights = np.array(weights)
        weights = weights / weights.sum()  # Normalize
        result = np.zeros(n, dtype=np.float64)
        for p, w in zip(preds, weights):
            result += w * p

        return result

    def _active_model_names(self) -> list:
        names = []
        if self._rf is not None:
            names.append("RandomForest")
        if self._gb is not None:
            names.append("GradientBoosting")
        if self._tcn_available:
            names.append("PhaseDriftTCN_CUDA")
        elif self._tcn is not None:
            names.append("PhaseDriftTCN_CPU_fallback")
        return names

    def save(self, path: str = None) -> str:
        """Save ensemble models to disk."""
        save_dir = Path(path) if path else self._model_dir
        save_dir.mkdir(parents=True, exist_ok=True)

        if HAS_SKLEARN:
            import joblib
            if self._rf is not None:
                joblib.dump(self._rf, save_dir / "rf_model.joblib")
            if self._gb is not None:
                joblib.dump(self._gb, save_dir / "gb_model.joblib")

        if self._tcn_available and self._tcn is not None:
            import torch
            torch.save(self._tcn.state_dict(), save_dir / "tcn_model.pt")

        log.info("Ensemble saved to %s", save_dir)
        return str(save_dir)

    def load(self, path: str = None) -> None:
        """Load ensemble models from disk."""
        load_dir = Path(path) if path else self._model_dir

        if HAS_SKLEARN:
            import joblib
            rf_path = load_dir / "rf_model.joblib"
            gb_path = load_dir / "gb_model.joblib"
            if rf_path.exists():
                self._rf = joblib.load(rf_path)
            if gb_path.exists():
                self._gb = joblib.load(gb_path)

        tcn_path = load_dir / "tcn_model.pt"
        if HAS_TORCH and tcn_path.exists():
            try:
                from ml.dcq_drift_model import PhaseDriftTCN
                self._tcn = PhaseDriftTCN(in_features=4)
                import torch
                self._tcn.load_state_dict(torch.load(tcn_path, map_location="cuda"))
                self._tcn = self._tcn.to("cuda").eval()
                self._tcn_available = True
            except Exception as e:
                log.warning("Failed to load TCN: %s", e)

        self._trained = (self._rf is not None or self._gb is not None)
        log.info("Ensemble loaded from %s", load_dir)

    def predict_scale_separated(self, features: np.ndarray) -> dict:
        """v4.3: Split feature time series at midpoint, compare halves.

        Inspired by Mellin digon t<->1/t isometric matching (GoZ Technique 3):
        for a stationary process, energy in UV half should match IR half.
        Asymmetry between halves IS the drift.

        UV (first half): high-frequency noise floor
        IR (second half): low-frequency drift signal

        Args:
            features: Shape (N, T, F) -- N samples, T timesteps, F features.

        Returns:
            Dict with drift_signal, noise_floor, drift_snr, stationary.
        """
        if features.ndim != 3:
            raise ValueError(
                f"Expected 3D array (N, T, F), got shape {features.shape}"
            )

        T = features.shape[1]
        if T < 2:
            return {
                "drift_signal": 0.0,
                "noise_floor": 0.0,
                "drift_snr": 0.0,
                "stationary": True,
            }

        mid = T // 2
        uv_features = features[:, :mid, :]   # first half
        ir_features = features[:, mid:, :]    # second half

        uv_stats = np.mean(uv_features, axis=1)  # (N, F)
        ir_stats = np.mean(ir_features, axis=1)   # (N, F)

        drift_signal = ir_stats - uv_stats  # difference IS drift
        noise_floor = (
            np.var(uv_features, axis=1) + np.var(ir_features, axis=1)
        ) / 2

        drift_snr = np.abs(drift_signal) / (np.sqrt(noise_floor) + 1e-10)

        return {
            "drift_signal": float(np.mean(drift_signal)),
            "noise_floor": float(np.mean(noise_floor)),
            "drift_snr": float(np.mean(drift_snr)),
            "stationary": bool(np.mean(drift_snr) < 1.0),
        }

    def status(self) -> dict:
        """Return current ensemble status."""
        return {
            "trained": self._trained,
            "models": self._active_model_names(),
            "cuda_available": HAS_TORCH,
            "sklearn_available": HAS_SKLEARN,
        }
