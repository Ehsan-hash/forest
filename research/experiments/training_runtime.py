"""Reusable FP32 training, validation, and checkpoint runtime for FOREST studies.

The runtime accepts explicit work/config/data-version identifiers and the full
effective recipe. It does not calculate or require content hashes. Dataset
loading is confined to the allowlisted researcher files below; evaluator and
raw-data directories are never searched.
"""
from __future__ import annotations

import argparse
import copy
import json
import itertools
import math
import os
import platform
import random
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import torch
from torch import Tensor, nn
from torch.nn import functional as F


RESEARCHER_FILES = {
    "darcy_train": "darcy64_train.npz",
    "darcy_validation": "darcy64_validation.npz",
    "blood_train": "bloodmnist64_train.npz",
    "blood_validation": "bloodmnist64_validation.npz",
    "etth": "etth1_train_validation.npz",
}
DEFAULT_MUTATION_SEED = 20261012


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_torch_save(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        torch.save(dict(payload), temp_name)
        with open(temp_name, "rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_npz(path: Path, **arrays: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            np.savez_compressed(stream, **arrays)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class SpectralConv2d(nn.Module):
    """Two-axis rFFT convolution with independent positive/negative kx bands."""

    def __init__(self, channels: int, modes: int):
        super().__init__()
        if channels < 1 or modes < 1:
            raise ValueError("channels and modes must be positive")
        scale = 1.0 / (channels * channels)
        self.modes = modes
        self.weight_pos = nn.Parameter(scale * torch.randn(channels, channels, modes, modes, dtype=torch.cfloat))
        self.weight_neg = nn.Parameter(scale * torch.randn(channels, channels, modes, modes, dtype=torch.cfloat))

    @staticmethod
    def _mix(x: Tensor, weight: Tensor) -> Tensor:
        return torch.einsum("bixy,ioxy->boxy", x, weight)

    def forward(self, x: Tensor) -> Tensor:
        b, _, h, w = x.shape
        transformed = torch.fft.rfft2(x.float()).to(torch.complex64)
        out = torch.zeros((b, self.weight_pos.shape[1], h, w // 2 + 1), dtype=torch.complex64, device=x.device)
        mx, my = min(self.modes, (h + 1) // 2), min(self.modes, w // 2 + 1)
        if mx and my:
            out[:, :, :mx, :my] = self._mix(transformed[:, :, :mx, :my], self.weight_pos[:, :, :mx, :my])
            out[:, :, -mx:, :my] = self._mix(transformed[:, :, -mx:, :my], self.weight_neg[:, :, :mx, :my])
        return torch.fft.irfft2(out, s=(h, w))


class FNO2d(nn.Module):
    def __init__(self, modes: int = 8, width: int = 24, blocks: int = 3):
        super().__init__()
        self.lift = nn.Conv2d(3, width, 1)
        self.spectral = nn.ModuleList(SpectralConv2d(width, modes) for _ in range(blocks))
        self.pointwise = nn.ModuleList(nn.Conv2d(width, width, 1) for _ in range(blocks))
        self.proj1 = nn.Conv2d(width, 64, 1)
        self.proj2 = nn.Conv2d(64, 1, 1)

    def forward(self, x: Tensor) -> Tensor:
        x = self.lift(x)
        for spectral, pointwise in zip(self.spectral, self.pointwise):
            x = F.gelu(spectral(x) + pointwise(x))
        return self.proj2(F.gelu(self.proj1(x)))


class TinyCNN6(nn.Module):
    def __init__(self, classes: int = 8):
        super().__init__()
        blocks: list[nn.Module] = []
        input_channels = 3
        for output_channels in (32, 64, 128):
            for _ in range(2):
                blocks.extend((nn.Conv2d(input_channels, output_channels, 3, padding=1, bias=False),
                               nn.GroupNorm(8, output_channels), nn.ReLU()))
                input_channels = output_channels
            blocks.append(nn.MaxPool2d(2, 2))
        self.features = nn.Sequential(*blocks)
        self.head = nn.Linear(128, classes)

    def forward(self, x: Tensor) -> Tensor:
        return self.head(F.adaptive_avg_pool2d(self.features(x), 1).flatten(1))


class WindowMLP(nn.Module):
    def __init__(self, context: int, features: int, dropout: float = 0.0, horizon: int = 24):
        super().__init__()
        self.fc1 = nn.Linear(context * features, 128)
        self.drop1 = nn.Dropout(dropout)
        self.fc2 = nn.Linear(128, 128)
        self.drop2 = nn.Dropout(dropout)
        self.out = nn.Linear(128, horizon)

    def forward(self, x: Tensor) -> Tensor:
        x = x.flatten(1)
        x = self.drop1(F.gelu(self.fc1(x)))
        x = self.drop2(F.gelu(self.fc2(x)))
        return self.out(x)


@dataclass
class PreparedData:
    x: Tensor
    y: Tensor
    validation_x: Tensor
    validation_y: Tensor
    metadata: dict[str, Any]


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.use_deterministic_algorithms(True)


def _npz(root: Path, key: str) -> dict[str, np.ndarray]:
    if key not in RESEARCHER_FILES:
        raise ValueError(f"Dataset is outside the researcher allowlist: {key}")
    path = root / RESEARCHER_FILES[key]
    if not path.is_file():
        raise FileNotFoundError(f"Researcher dataset file is missing: {path}")
    with np.load(path, allow_pickle=False) as archive:
        return {name: archive[name] for name in archive.files}


def _select_rows(archive: Mapping[str, np.ndarray], ids: Sequence[int], *, id_key: str = "sample_id") -> np.ndarray:
    source_ids = np.asarray(archive[id_key]).reshape(-1)
    index = {int(sample_id): row for row, sample_id in enumerate(source_ids)}
    requested = [int(x) for x in ids]
    if len(index) != len(source_ids):
        raise ValueError("Researcher split contains duplicate stable sample IDs")
    missing = [x for x in requested if x not in index]
    if missing:
        raise ValueError(f"Requested IDs are missing from the declared researcher split: {missing[:8]}")
    if len(set(requested)) != len(requested):
        raise ValueError("Configured sample IDs must be unique")
    return np.asarray([index[x] for x in requested], dtype=np.int64)


def _noise_like_by_id(x: Tensor, ids: Sequence[Any], *, seed: int, std: float, clip: tuple[float, float] | None = None) -> Tensor:
    """Generate a fixed per-sample transform from seed and stable ID order."""
    if std <= 0:
        return x
    rng = np.random.default_rng(seed)
    out = x.clone()
    for i, _sample_id in enumerate(ids):
        noise = torch.from_numpy(rng.normal(0.0, std, size=tuple(out[i].shape)).astype(np.float32))
        out[i] += noise
    if clip is not None:
        out.clamp_(clip[0], clip[1])
    return out


def prepare_darcy(root: Path, config: Mapping[str, Any], *, dev_selection: Mapping[str, Any] | None = None) -> PreparedData:
    train = _npz(root, "darcy_train")
    val = _npz(root, "darcy_validation")
    if dev_selection is not None:
        train_ids = list(map(int, dev_selection["samples"]["D"]["train"]))
        val_ids = list(map(int, dev_selection["samples"]["D"]["validation"]))
    else:
        train_ids = list(map(int, config.get("train_sample_ids", train["sample_id"].tolist())))
        val_ids = list(map(int, config.get("validation_sample_ids", val["sample_id"].tolist())))
    if set(train_ids) & set(val_ids):
        raise ValueError("Darcy training and validation IDs must not overlap")
    n = config.get("training_sample_count")
    if n is not None:
        train_ids = train_ids[: int(n)]
    tr_i, va_i = _select_rows(train, train_ids), _select_rows(val, val_ids)
    raw_x = torch.from_numpy(train["x"][tr_i].astype(np.float32))
    raw_y = torch.from_numpy(train["y"][tr_i].astype(np.float32))
    raw_vx = torch.from_numpy(val["x"][va_i].astype(np.float32))
    raw_vy = torch.from_numpy(val["y"][va_i].astype(np.float32))
    x_mean = raw_x.mean().item()
    x_std = max(raw_x.std(unbiased=False).item(), 1e-6)
    y_mean = raw_y.mean().item()
    y_std = max(raw_y.std(unbiased=False).item(), 1e-6)
    def inputs(field: Tensor) -> Tensor:
        n_samples, height, width = field.shape
        gx = torch.linspace(0, 1, height).view(1, 1, height, 1).expand(n_samples, 1, height, width)
        gy = torch.linspace(0, 1, width).view(1, 1, 1, width).expand(n_samples, 1, height, width)
        return torch.cat((((field - x_mean) / x_std)[:, None], gx, gy), dim=1)
    x, vx = inputs(raw_x), inputs(raw_vx)
    noise_sd = float(config.get("validation_noise_std", 0.0)) * x_std
    if noise_sd:
        noisy_coeff = _noise_like_by_id(raw_vx[:, None], val_ids, seed=int(config.get("validation_noise_seed", 0)), std=noise_sd)[:, 0]
        vx = inputs(noisy_coeff)
    metadata = {
        "dataset_version_id": config.get("dataset_version_id", "darcy64-researcher-v1"),
        "train_sample_ids": train_ids, "validation_sample_ids": val_ids,
        "input_coefficient_mean": x_mean, "input_coefficient_std": x_std,
        "target_mean": y_mean, "target_std": y_std,
        "normalization_fit_sample_ids": train_ids,
        "validation_input_transform": {"gaussian_sd_in_input_std_units": float(config.get("validation_noise_std", 0.0)), "seed": int(config.get("validation_noise_seed", 0))},
    }
    return PreparedData(x, ((raw_y - y_mean) / y_std)[:, None], vx, ((raw_vy - y_mean) / y_std)[:, None], metadata)


def _resize_images(images: np.ndarray, resolution: int) -> Tensor:
    image_tensor = torch.from_numpy(images.transpose(0, 3, 1, 2).copy()).float().div_(255.0)
    return _resize_image_tensor(image_tensor, resolution)


def _resize_image_tensor(image_tensor: Tensor, resolution: int) -> Tensor:
    if resolution != 64:
        image_tensor = F.interpolate(image_tensor, size=(resolution, resolution), mode="bilinear", align_corners=False, antialias=True)
    return image_tensor


def prepare_blood(root: Path, config: Mapping[str, Any], *, dev_selection: Mapping[str, Any] | None = None) -> PreparedData:
    train, val = _npz(root, "blood_train"), _npz(root, "blood_validation")
    train_ids = list(map(int, dev_selection["samples"]["M"]["train"])) if dev_selection else list(map(int, config.get("train_sample_ids", train["sample_id"].tolist())))
    val_ids = list(map(int, dev_selection["samples"]["M"]["validation"])) if dev_selection else list(map(int, config.get("validation_sample_ids", val["sample_id"].tolist())))
    if config.get("training_sample_count") is not None:
        train_ids = train_ids[: int(config["training_sample_count"])]
    train_idx, val_idx = _select_rows(train, train_ids), _select_rows(val, val_ids)
    labels = train["labels"][train_idx].astype(np.int64).reshape(-1)
    val_labels = val["labels"][val_idx].astype(np.int64).reshape(-1)
    classes = int(config.get("class_count", 8))
    if np.any(labels < 0) or np.any(labels >= classes) or np.any(val_labels < 0) or np.any(val_labels >= classes):
        raise ValueError("BloodMNIST labels are outside the declared fixed class set")
    resolution = int(config.get("resolution", 64))
    raw_train, raw_val = _resize_images(train["images"][train_idx], resolution), _resize_images(val["images"][val_idx], resolution)
    mean = raw_train.mean((0, 2, 3), keepdim=True)
    std = raw_train.std((0, 2, 3), unbiased=False, keepdim=True).clamp_min(1e-6)
    # For the registered C2 question, perturb source-scale validation pixels
    # before resize; normalizing and resizing remain the same across candidates.
    if float(config.get("validation_noise_std", 0.0)):
        source_val = torch.from_numpy(val["images"][val_idx].transpose(0, 3, 1, 2).copy()).float().div_(255.0)
        source_val = _noise_like_by_id(source_val, val_ids, seed=int(config.get("validation_noise_seed", 0)), std=float(config["validation_noise_std"]), clip=(0.0, 1.0))
        raw_val = _resize_image_tensor(source_val, resolution)
    x, vx = (raw_train - mean) / std, (raw_val - mean) / std
    count = np.bincount(labels, minlength=classes)
    weighting = str(config.get("class_weighting", "none"))
    if weighting == "none":
        class_weights = np.ones(classes, dtype=np.float64)
    elif weighting == "inverse_sqrt_frequency":
        class_weights = 1.0 / np.sqrt(np.maximum(count, 1))
    elif weighting == "inverse_frequency":
        class_weights = 1.0 / np.maximum(count, 1)
    else:
        raise ValueError(f"Unknown class weighting: {weighting}")
    class_weights = class_weights / class_weights.mean()
    metadata = {
        "dataset_version_id": config.get("dataset_version_id", "bloodmnist64-researcher-v1"),
        "train_sample_ids": train_ids, "validation_sample_ids": val_ids,
        "normalization_mean": mean.flatten().tolist(), "normalization_std": std.flatten().tolist(),
        "normalization_fit_sample_ids": train_ids,
        "class_counts": count.tolist(), "class_weights": class_weights.tolist(),
        "resolution": resolution, "resize": "bilinear_align_corners_false_antialias_true",
        "validation_input_transform": {"pixel_noise_sd_0_to_1": float(config.get("validation_noise_std", 0.0)), "seed": int(config.get("validation_noise_seed", 0))},
    }
    return PreparedData(x, torch.from_numpy(labels), vx, torch.from_numpy(val_labels), metadata)


FEATURE_SETS = {
    "ot_only": [6],
    "high_load_plus_ot": [0, 2, 6],
    "all_7": [0, 1, 2, 3, 4, 5, 6],
}
ETT_COLUMNS = ["HUFL", "HULL", "MUFL", "MULL", "LUFL", "LULL", "OT"]


def _origins(config: Mapping[str, Any], split: str) -> list[int]:
    provided = config.get(f"{split}_origins")
    if provided is not None:
        return list(map(int, provided))
    stride = int(config.get("origin_stride", 1))
    if split == "train":
        return list(range(192, 8617, stride))
    return list(range(8640, 11497, stride))


def prepare_etth1(root: Path, config: Mapping[str, Any], *, dev_selection: Mapping[str, Any] | None = None) -> PreparedData:
    archive = _npz(root, "etth")
    columns = [str(value) for value in archive["columns"].tolist()]
    if columns != ETT_COLUMNS:
        raise ValueError(f"ETTh1 feature order mismatch: {columns}")
    train = archive["train_values"].astype(np.float64)
    validation = archive["validation_values"].astype(np.float64)
    values = np.concatenate((train, validation), axis=0)
    context = int(config.get("context_length", 96))
    horizon = int(config.get("forecast_horizon", 24))
    features = FEATURE_SETS[str(config.get("feature_set", "all_7"))]
    train_origins = _origins(config, "train")
    val_origins = _origins(config, "validation")
    if config.get("training_sample_count") is not None:
        train_origins = train_origins[: int(config["training_sample_count"])]
    if dev_selection is not None:
        train_origins = list(range(192, 2208, 16))
        val_origins = list(range(2304, 2816, 16))
        context, horizon, features = 96, 24, FEATURE_SETS["all_7"]
    # The default formal scaler uses only the legal 0:8640 training interval.
    # A removed C3 interval remains a gap; it is never concatenated away.
    scale_rows = np.arange(0, 8640)
    removed = config.get("excluded_training_row_interval")
    if removed:
        a, b = map(int, removed)
        scale_rows = scale_rows[(scale_rows < a) | (scale_rows >= b)]
    custom_rows = config.get("normalization_row_ids")
    if custom_rows is not None:
        scale_rows = np.asarray(custom_rows, dtype=np.int64)
    if dev_selection is not None:
        scaler = dev_selection["samples"]["T"]["scaler"]
        mean = np.asarray(scaler["mean"], dtype=np.float64)
        std = np.asarray(scaler["std_population"], dtype=np.float64)
        scale_rows = np.arange(96, 2192)
    else:
        mean = train[scale_rows].mean(axis=0)
        std = train[scale_rows].std(axis=0, ddof=0)
    std = np.maximum(std, 1e-6)
    if removed:
        ra, rb = map(int, removed)
        train_origins = [origin for origin in train_origins if not (origin - 192 < rb and origin + horizon > ra)]
    if len(train_origins) < 1 or len(val_origins) < 1:
        raise ValueError("ETTh1 needs at least one train and one validation origin")
    def make_windows(origins: Sequence[int], split: str) -> tuple[np.ndarray, np.ndarray]:
        xs: list[np.ndarray] = []
        ys: list[np.ndarray] = []
        for origin in origins:
            if origin < context or origin + horizon > len(values):
                raise ValueError(f"ETTh1 window out of available researcher range: origin={origin}")
            if split == "train" and not (0 <= origin and origin + horizon <= 8640):
                raise ValueError("ETTh1 training targets must remain inside rows [0,8640)")
            validation_bounds = (0, 8640) if dev_selection is not None else (8640, 11520)
            if split == "validation" and not (validation_bounds[0] <= origin and origin + horizon <= validation_bounds[1]):
                raise ValueError(f"ETTh1 validation targets must remain inside rows [{validation_bounds[0]},{validation_bounds[1]})")
            xs.append(((values[origin-context:origin][:, features] - mean[features]) / std[features]).astype(np.float32))
            ys.append(((values[origin:origin+horizon, 6] - mean[6]) / std[6]).astype(np.float32))
        return np.asarray(xs, np.float32), np.asarray(ys, np.float32)
    x, y = make_windows(train_origins, "train")
    vx, vy = make_windows(val_origins, "validation")
    noise_scale = float(config.get("validation_noise_std_in_training_sd", 0.0))
    noise_seed = int(config.get("validation_noise_seed", 0))
    noise_matrix = np.random.default_rng(noise_seed).normal(0.0, noise_scale, size=values.shape).astype(np.float32) if noise_scale else None
    if noise_matrix is not None:
        vx_rows = []
        for origin in val_origins:
            history_noise = noise_matrix[origin-context:origin][:, features]
            vx_rows.append(((values[origin-context:origin][:, features] - mean[features]) / std[features] + history_noise).astype(np.float32))
        vx = np.asarray(vx_rows, dtype=np.float32)
    meta = {
        "dataset_version_id": config.get("dataset_version_id", "etth1-researcher-v1"),
        "train_origins": train_origins, "validation_origins": val_origins,
        "train_sample_ids": [f"origin:{origin}" for origin in train_origins],
        "validation_sample_ids": [f"origin:{origin}" for origin in val_origins],
        "normalization_row_ids": scale_rows.tolist(), "normalization_mean": mean.tolist(), "normalization_std_population": std.tolist(),
        "columns": columns, "feature_indices": features, "target": "OT", "context_length": context,
        "forecast_horizon": horizon, "excluded_training_row_interval": removed,
        "validation_input_transform": {"history_noise_std_in_training_column_sd": noise_scale, "seed": int(config.get("validation_noise_seed", 0))},
    }
    return PreparedData(torch.from_numpy(x), torch.from_numpy(y), torch.from_numpy(vx), torch.from_numpy(vy), meta)


def prepare_data(family: str, data_root: Path, config: Mapping[str, Any], *, dev_selection: Mapping[str, Any] | None = None) -> PreparedData:
    return {
        "darcy": prepare_darcy,
        "bloodmnist": prepare_blood,
        "etth1": prepare_etth1,
    }[family](data_root, config, dev_selection=dev_selection)


def researcher_data_selection(data_root: Path) -> dict[str, dict[str, Any]]:
    """Return explicit ordered train/validation IDs from researcher-only files."""
    darcy_train = _npz(data_root, "darcy_train")["sample_id"].astype(np.int64).tolist()
    darcy_validation = _npz(data_root, "darcy_validation")["sample_id"].astype(np.int64).tolist()
    blood_train = _npz(data_root, "blood_train")["sample_id"].astype(np.int64).tolist()
    blood_validation = _npz(data_root, "blood_validation")["sample_id"].astype(np.int64).tolist()
    return {
        "darcy": {"train_sample_ids": darcy_train, "validation_sample_ids": darcy_validation},
        "bloodmnist": {"train_sample_ids": blood_train, "validation_sample_ids": blood_validation},
        "etth1": {"train_origins": list(range(192, 8617)), "validation_origins": list(range(8640, 11497))},
    }


def researcher_c3_labels(data_root: Path) -> dict[int, int]:
    """Read BloodMNIST labels from the allowlisted researcher training split."""
    archive = _npz(data_root, "blood_train")
    sample_ids = archive["sample_id"].astype(np.int64).tolist()
    labels = archive["labels"].astype(np.int64).reshape(-1).tolist()
    if len(sample_ids) != len(labels):
        raise ValueError("Researcher BloodMNIST IDs and labels have different lengths")
    return dict(zip(sample_ids, labels))


def build_model(family: str, config: Mapping[str, Any]) -> nn.Module:
    if family == "darcy":
        return FNO2d(int(config.get("fourier_modes_per_axis", 8)), int(config.get("hidden_width", 24)), int(config.get("spectral_layers", 3)))
    if family == "bloodmnist":
        return TinyCNN6(int(config.get("class_count", 8)))
    if family == "etth1":
        return WindowMLP(int(config.get("context_length", 96)), len(FEATURE_SETS[str(config.get("feature_set", "all_7"))]), float(config.get("dropout", 0.0)), int(config.get("forecast_horizon", 24)))
    raise ValueError(f"Unknown family: {family}")


def _gradient_relative_l2(pred: Tensor, target: Tensor) -> Tensor:
    spacing = 1.0 / 63.0
    pred_dx, target_dx = (pred[..., 1:, :] - pred[..., :-1, :]) / spacing, (target[..., 1:, :] - target[..., :-1, :]) / spacing
    pred_dy, target_dy = (pred[..., :, 1:] - pred[..., :, :-1]) / spacing, (target[..., :, 1:] - target[..., :, :-1]) / spacing
    out = []
    for i in range(len(pred)):
        error = torch.cat((pred_dx[i].reshape(-1) - target_dx[i].reshape(-1), pred_dy[i].reshape(-1) - target_dy[i].reshape(-1)))
        truth = torch.cat((target_dx[i].reshape(-1), target_dy[i].reshape(-1)))
        out.append(torch.linalg.vector_norm(error) / torch.linalg.vector_norm(truth).clamp_min(1e-12))
    return torch.stack(out).mean()


def _objective(family: str, model: nn.Module, pred: Tensor, target: Tensor, config: Mapping[str, Any], metadata: Mapping[str, Any]) -> Tensor:
    if family == "darcy":
        mean, std = float(metadata["target_mean"]), float(metadata["target_std"])
        pred_original, target_original = pred * std + mean, target * std + mean
        per_sample = torch.linalg.vector_norm((pred_original - target_original).flatten(1), dim=1) / torch.linalg.vector_norm(target_original.flatten(1), dim=1).clamp_min(1e-12)
        loss = per_sample.mean()
        gradient_weight = float(config.get("gradient_loss_weight", 0.0))
        if gradient_weight:
            loss = loss + gradient_weight * _gradient_relative_l2(pred_original, target_original)
        c4_weight = float(config.get("c4_gradient_mse_weight", 0.0))
        if c4_weight:
            dxp = (pred_original[..., 1:, :] - pred_original[..., :-1, :]) * 63
            dyp = (pred_original[..., :, 1:] - pred_original[..., :, :-1]) * 63
            dxt = (target_original[..., 1:, :] - target_original[..., :-1, :]) * 63
            dyt = (target_original[..., :, 1:] - target_original[..., :, :-1]) * 63
            dx_error, dy_error = (dxp - dxt).square(), (dyp - dyt).square()
            gradient_mse = (dx_error.sum() + dy_error.sum()) / (dx_error.numel() + dy_error.numel())
            loss = loss + c4_weight * gradient_mse
    elif family == "bloodmnist":
        weights = torch.as_tensor(metadata["class_weights"], device=pred.device, dtype=pred.dtype)
        label_smoothing = float(config.get("label_smoothing", 0.0))
        loss = F.cross_entropy(pred, target.long(), weight=weights, label_smoothing=label_smoothing)
    else:
        name = str(config.get("loss_name", "mse"))
        if name == "mse":
            loss = F.mse_loss(pred, target)
        elif name == "huber":
            loss = F.huber_loss(pred, target, delta=float(config.get("huber_delta", 1.0)))
        else:
            raise ValueError(f"Unknown ETTh1 training loss: {name}")
    c4_l2 = float(config.get("c4_weight_l2", 0.0))
    if c4_l2:
        trainable_weights = [param for name, param in model.named_parameters()
                             if param.requires_grad and (name.endswith("weight") or ".weight" in name)]
        squared_sum = torch.stack([param.square().sum() for param in trainable_weights]).sum()
        element_count = sum(param.numel() for param in trainable_weights)
        loss = loss + c4_l2 * squared_sum / element_count
    return loss


def score_predictions(family: str, predictions: np.ndarray | Tensor, data: PreparedData) -> dict[str, float]:
    """Score saved validation predictions without running a new model pass."""
    pred = torch.as_tensor(predictions).detach().cpu()
    if len(pred) != len(data.validation_y):
        raise ValueError("Saved validation predictions are not aligned to the prepared validation IDs")
    if family == "darcy":
        p = pred.double()
        mean, std = float(data.metadata["target_mean"]), float(data.metadata["target_std"])
        y = data.validation_y.double() * std + mean
        relative = torch.linalg.vector_norm((p-y).flatten(1), dim=1) / torch.linalg.vector_norm(y.flatten(1), dim=1).clamp_min(1e-12)
        metrics = {"relative_l2": float(relative.mean())}
        metrics["relative_gradient_l2"] = float(_gradient_relative_l2(p, y))
        return metrics
    if family == "bloodmnist":
        logits, labels = pred.double(), data.validation_y.long()
        predicted = logits.argmax(1)
        confusion = torch.bincount(labels * 8 + predicted, minlength=64).reshape(8, 8).double()
        true_positive = confusion.diag()
        denom = 2 * true_positive + confusion.sum(0) + confusion.sum(1) - 2 * true_positive
        f1 = torch.where(denom > 0, 2 * true_positive / denom, 0.0)
        row_totals = confusion.sum(1)
        if torch.any(row_totals == 0):
            raise ValueError("Validation split is missing a fixed class required for balanced accuracy")
        metrics = {
            "accuracy": float((predicted == labels).double().mean()),
            "macro_f1": float(f1.mean()),
            "balanced_accuracy": float((true_positive / row_totals).mean()),
            "nll": float(F.cross_entropy(logits, labels)),
        }
        return metrics
    mean, std = float(data.metadata["normalization_mean"][6]), float(data.metadata["normalization_std_population"][6])
    p, y = pred.double(), data.validation_y.double() * std + mean
    difference = p - y
    return {"mae": float(difference.abs().mean()), "rmse": float(difference.square().mean().sqrt())}


def evaluate(family: str, model: nn.Module, data: PreparedData, config: Mapping[str, Any], device: torch.device) -> tuple[dict[str, float], np.ndarray]:
    model.eval()
    batch_size = int(config["batch_size"])
    predictions = []
    with torch.inference_mode():
        for start in range(0, len(data.validation_x), batch_size):
            predictions.append(model(data.validation_x[start:start + batch_size].to(device)).float().cpu())
    pred = torch.cat(predictions)
    if family == "darcy":
        mean, std = float(data.metadata["target_mean"]), float(data.metadata["target_std"])
        raw_predictions = (pred * std + mean).numpy()
    elif family == "bloodmnist":
        raw_predictions = pred.numpy()
    else:
        mean, std = float(data.metadata["normalization_mean"][6]), float(data.metadata["normalization_std_population"][6])
        raw_predictions = (pred * std + mean).numpy()
    return score_predictions(family, raw_predictions, data), raw_predictions


def _rng_state() -> dict[str, Any]:
    return {"python": random.getstate(), "numpy": np.random.get_state(), "torch_cpu": torch.get_rng_state(),
            "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []}


def _restore_rng(state: Mapping[str, Any]) -> None:
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch_cpu"])
    if torch.cuda.is_available() and state.get("torch_cuda"):
        torch.cuda.set_rng_state_all(state["torch_cuda"])


def validate_config(config: Mapping[str, Any]) -> None:
    required = ("work_id", "phase", "task_id", "configuration_id", "dataset_version_id", "family", "seed", "optimizer_updates", "batch_size")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"Training config is missing required explicit fields: {missing}")
    if config["phase"] == "pilot" and config.get("formal_score_eligible") is not False:
        raise ValueError("Pilot work must explicitly declare formal_score_eligible=false")
        if config["phase"] == "calibration" and config.get("formal_score_eligible") is not False:
            raise ValueError("Calibration work must explicitly declare formal_score_eligible=false")
    if config["phase"] not in {"pilot", "calibration", "formal", "runtime_regression"}:
        raise ValueError(f"Unsupported fit phase: {config['phase']}")
    if int(config["optimizer_updates"]) < 1 or int(config["batch_size"]) < 1:
        raise ValueError("optimizer_updates and batch_size must be positive")
    if int(config.get("effective_batch_size", config["batch_size"])) != int(config["batch_size"]):
        raise NotImplementedError("Gradient accumulation is not implemented; physical/effective batch sizes must match")
    if config["family"] == "etth1" and config.get("feature_set", "all_7") not in FEATURE_SETS:
        raise ValueError("ETTh1 feature_set must be one of the declared fixed feature sets")
    if config["phase"] in {"pilot", "calibration", "formal"}:
        if not isinstance(config.get("recipe_version_id"), str) or not config["recipe_version_id"].strip():
            raise ValueError("Scientific configs need an explicit recipe_version_id")
        optimizer = config.get("optimizer")
        if not isinstance(optimizer, Mapping) or optimizer.get("name") != "AdamW":
            raise ValueError("Scientific configs must explicitly declare AdamW")
        # Recipe values are explicit inputs to each fit. Keep the optimizer
        # family supported, while permitting a documented, versioned recipe
        # change without editing this runtime.
        try:
            lr = float(optimizer["lr"])
            betas = [float(value) for value in optimizer["betas"]]
            eps = float(optimizer["eps"])
            weight_decay = float(optimizer["weight_decay"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Scientific AdamW config needs explicit lr, betas, eps, and weight_decay") from exc
        if (not math.isfinite(lr) or lr <= 0 or len(betas) != 2
                or not all(math.isfinite(value) and 0 <= value < 1 for value in betas)
                or not math.isfinite(eps) or eps <= 0
                or not math.isfinite(weight_decay) or weight_decay < 0):
            raise ValueError("Scientific AdamW values are outside the supported finite ranges")
        scheduler = config.get("scheduler")
        try:
            final_lr_fraction = float(scheduler["final_lr_fraction"])
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError("Scientific configs need an explicit supported scheduler and final LR fraction") from exc
        if scheduler.get("name") != "cosine" or not math.isfinite(final_lr_fraction) or not 0 <= final_lr_fraction <= 1:
            raise ValueError("Scientific configs currently support cosine decay with a finite final LR fraction in [0,1]")
        if config.get("precision") != "float32" or not isinstance(config.get("tf32"), bool):
            raise ValueError("Scientific configs currently support float32 and an explicit TF32 setting")
        if int(config.get("log_every_updates", 25)) != 25:
            raise ValueError("Scientific training observations must be logged every 25 updates")
        if config["phase"] == "calibration":
            if not isinstance(config.get("calibration_role"), str) or not config["calibration_role"]:
                raise ValueError("Calibration configs need an explicit calibration_role")
            allowed = config.get("allowed_update_counts")
            if not isinstance(allowed, list) or int(config["optimizer_updates"]) not in {int(x) for x in allowed}:
                raise ValueError("Calibration optimizer_updates must be one of the explicit allowed_update_counts")
        if config["family"] == "etth1":
            ids_present = "train_origins" in config and "validation_origins" in config
        else:
            ids_present = "train_sample_ids" in config and "validation_sample_ids" in config
        if not ids_present:
            raise ValueError("Scientific fits must carry their ordered training and validation IDs in the explicit config")


def validate_calibration_bundle(
    configs: Sequence[Mapping[str, Any]],
    required_roles_by_family: Mapping[str, Sequence[str]],
) -> None:
    """Check an explicitly decided calibration set before scheduling its fits.

    Callers supply the required calibration roles and complete per-fit configs;
    this helper does not infer a candidate, choose an update level, or alter a
    research matrix.
    """
    observed: dict[str, set[str]] = {}
    seen_work_ids: set[str] = set()
    for config in configs:
        validate_config(config)
        if config.get("phase") != "calibration":
            raise ValueError("Calibration bundle entries must use phase='calibration'")
        role = config.get("calibration_role")
        if not isinstance(role, str) or not role:
            raise ValueError("Calibration entries need an explicit calibration_role")
        work_id = str(config["work_id"])
        if work_id in seen_work_ids:
            raise ValueError(f"Duplicate calibration work_id: {work_id}")
        seen_work_ids.add(work_id)
        allowed = config.get("allowed_update_counts")
        if allowed is not None and int(config["optimizer_updates"]) not in {int(x) for x in allowed}:
            raise ValueError(f"Calibration updates are not among the explicit allowed options for {work_id}")
        family_roles = observed.setdefault(str(config["family"]), set())
        if role in family_roles:
            raise ValueError(f"Duplicate calibration role {role} for {config['family']}")
        family_roles.add(role)
    for family, roles in required_roles_by_family.items():
        missing = set(roles) - observed.get(family, set())
        if missing:
            raise ValueError(f"Calibration bundle is missing {family} roles: {sorted(missing)}")


def _load_source_documents(source_spec_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    models = json.loads((source_spec_dir / "models.json").read_text(encoding="utf-8"))
    tasks = json.loads((source_spec_dir / "tasks.json").read_text(encoding="utf-8"))
    return models, tasks


def _family_model(models_doc: Mapping[str, Any], family: str) -> Mapping[str, Any]:
    for model in models_doc["models"]:
        if model["family_id"] == family:
            return model
    raise ValueError(f"No model recipe is declared for family {family}")


def _base_family_recipe(model: Mapping[str, Any]) -> dict[str, Any]:
    family = str(model["family_id"])
    architecture = model["architecture"]
    default = model["default_recipe"]
    if family == "darcy":
        return {
            "fourier_modes_per_axis": int(architecture["fourier_modes_per_axis"]),
            "hidden_width": int(architecture["hidden_width"]),
            "spectral_layers": int(architecture["spectral_layers"]),
            "gradient_loss_weight": float(default.get("gradient_loss_weight", 0.0)),
            "training_sample_count": int(default["training_sample_count"]),
        }
    if family == "bloodmnist":
        return {
            "resolution": int(default["resolution"]), "augmentation": str(default["augmentation"]),
            "label_smoothing": float(default["label_smoothing"]), "class_weighting": str(default["class_weighting"]),
            "class_count": 8,
        }
    if family == "etth1":
        return {
            "context_length": int(default["context_length"]), "feature_set": str(default["feature_set"]),
            "forecast_horizon": int(default["forecast_horizon"]), "loss_name": str(default["loss_name"]),
            "huber_delta": default.get("huber_delta"), "dropout": float(default["dropout"]),
        }
    raise ValueError(f"Unsupported model family: {family}")


def _resolve_data_ids(family: str, data_ids: Mapping[str, Any]) -> dict[str, Any]:
    key_names = {
        "darcy": ("train_sample_ids", "validation_sample_ids"),
        "bloodmnist": ("train_sample_ids", "validation_sample_ids"),
        "etth1": ("train_origins", "validation_origins"),
    }[family]
    missing = [key for key in key_names if key not in data_ids]
    if missing:
        raise ValueError(f"Explicit {family} data selection is missing: {missing}")
    result = {key: list(data_ids[key]) for key in key_names}
    for optional in ("excluded_training_row_interval", "normalization_row_ids"):
        if optional in data_ids:
            result[optional] = copy.deepcopy(data_ids[optional])
    for key in key_names:
        if not result[key]:
            raise ValueError(f"Explicit {family} selection {key} cannot be empty")
    return result


def build_fit_config(
    *, source_spec_dir: Path, task_id: str, configuration_id: str, seed: int,
    optimizer_updates: int, dataset_version_id: str, data_ids: Mapping[str, Any],
    phase: str = "formal", work_id: str | None = None,
    configuration_overrides: Mapping[str, Any] | None = None,
    recipe_version_id: str | None = None,
    recipe_overrides: Mapping[str, Any] | None = None,
    calibration_role: str | None = None, allowed_update_counts: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Build a full explicit per-fit config from the current task/model spec."""
    models_doc, tasks_doc = _load_source_documents(source_spec_dir)
    task = next((item for item in tasks_doc["tasks"] if item["task_id"] == task_id), None)
    if task is None:
        raise ValueError(f"Unknown formal task ID: {task_id}")
    family = str(task["family_id"])
    model = _family_model(models_doc, family)
    default_recipe = model["default_recipe"]
    effective_recipe_version = recipe_version_id or str(model.get("recipe_version_id", "source-spec-current"))
    effective_recipe = {
        "optimizer": {"name": "AdamW", "lr": 0.001, "betas": [0.9, 0.999], "eps": 1e-8, "weight_decay": 1e-4},
        "scheduler": {"name": "cosine", "final_lr_fraction": 0.1},
        "precision": "float32", "tf32": False, "log_every_updates": 25,
    }
    effective_recipe.update(copy.deepcopy(dict(recipe_overrides or {})))
    recipe_contract_fields = {"optimizer", "scheduler", "precision", "tf32", "log_every_updates"}
    changed_recipe_fields = recipe_contract_fields & (
        set((recipe_overrides or {}).keys()) | set((configuration_overrides or {}).keys())
    )
    if changed_recipe_fields and not recipe_version_id and "recipe_version_id" not in model:
        raise ValueError("A changed scientific recipe requires an explicit recipe_version_id")
    update_options = [int(value) for value in model["allowed_pre_freeze_update_counts"]]
    if int(optimizer_updates) not in update_options:
        raise ValueError(f"Update count {optimizer_updates} is not allowed for {family}: {update_options}")
    fit_config: dict[str, Any] = {
        "work_id": work_id or f"{task_id}-{configuration_id}-seed{int(seed)}-{phase}",
        "phase": phase, "formal_score_eligible": phase == "formal",
        "task_id": task_id, "configuration_id": configuration_id,
        "model_id": model["model_id"], "family": family,
        "dataset_version_id": dataset_version_id,
        "recipe_version_id": effective_recipe_version,
        "seed": int(seed), "optimizer_updates": int(optimizer_updates),
        "batch_size": int(default_recipe["effective_batch_size"]),
        "effective_batch_size": int(default_recipe["effective_batch_size"]),
        **effective_recipe,
        **_base_family_recipe(model), **_resolve_data_ids(family, data_ids),
        "registered_evaluation_contract": {
            "scientific_question": task["scientific_question"],
            "primary_metric": task["primary_metric"],
            "primary_direction": task["primary_direction"],
            "effect_threshold": copy.deepcopy(task["effect_threshold"]),
            "side_constraints": copy.deepcopy(task["side_constraints"]),
        },
        "configuration_overrides": copy.deepcopy(dict(configuration_overrides or {})),
    }
    immutable_fields = ("work_id", "phase", "formal_score_eligible", "task_id", "configuration_id", "model_id", "family",
                        "dataset_version_id", "seed", "optimizer_updates", "batch_size", "effective_batch_size",
                        "registered_evaluation_contract")
    original_identity = {field: copy.deepcopy(fit_config[field]) for field in immutable_fields}
    fit_config.update(copy.deepcopy(dict(configuration_overrides or {})))
    for field in immutable_fields:
        if fit_config[field] != original_identity[field]:
            raise ValueError(f"Configuration overrides may not change required fit identity/recipe field {field}")
    if phase == "calibration":
        if not calibration_role:
            raise ValueError("Calibration fits need an explicit calibration_role")
        fit_config["calibration_role"] = calibration_role
        fit_config["allowed_update_counts"] = [int(x) for x in (allowed_update_counts or update_options)]
    validate_config(fit_config)
    return fit_config


def build_snapshot_fit_configs(
    *, source_spec_dir: Path, dataset_version_ids: Mapping[str, str],
    data_ids_by_family: Mapping[str, Mapping[str, Any]],
    update_counts_by_family: Mapping[str, int], seeds: Sequence[int] = (1001, 1002),
) -> list[dict[str, Any]]:
    """Build the current 12-task snapshot fit plan (3 candidates x 2 seeds)."""
    _models_doc, tasks_doc = _load_source_documents(source_spec_dir)
    configs = []
    for task in tasks_doc["tasks"]:
        family, task_id = str(task["family_id"]), str(task["task_id"])
        if family not in dataset_version_ids or family not in data_ids_by_family or family not in update_counts_by_family:
            raise ValueError(f"Missing explicit data-version, ID selection, or update count for {family}")
        for candidate in task["snapshot_configurations"]:
            overrides = copy.deepcopy(candidate["overrides"])
            for seed in seeds:
                configs.append(build_fit_config(
                    source_spec_dir=source_spec_dir, task_id=task_id,
                    configuration_id=str(candidate["configuration_id"]), seed=int(seed),
                    optimizer_updates=int(update_counts_by_family[family]),
                    dataset_version_id=dataset_version_ids[family], data_ids=data_ids_by_family[family],
                    phase="formal", configuration_overrides=overrides,
                ))
                configs[-1]["is_anchor"] = bool(candidate.get("is_anchor", False))
    return configs


def enumerate_long_configurations(*, source_spec_dir: Path, task_id: str) -> list[dict[str, Any]]:
    """Enumerate the currently declared nine-configuration spaces without fitting."""
    _models_doc, tasks_doc = _load_source_documents(source_spec_dir)
    task = next((item for item in tasks_doc["tasks"] if item["task_id"] == task_id), None)
    if task is None:
        raise ValueError(f"Unknown formal task ID: {task_id}")
    long_spec = task.get("long_from_scratch")
    if not long_spec:
        raise ValueError(f"Task {task_id} has no registered long exploration space")
    axes = long_spec["cartesian_axes"]
    names = list(axes)
    configurations = []
    for values in itertools.product(*(axes[name] for name in names)):
        overrides = dict(zip(names, values))
        configurations.append({"configuration_id": f"{task_id}_long_" + "_".join(f"{key}-{value}" for key, value in overrides.items()),
                               "overrides": overrides,
                               "is_anchor": overrides == long_spec["anchor_overrides"]})
    if len(configurations) != int(long_spec["configuration_count"]):
        raise ValueError(f"Declared long configuration count mismatch for {task_id}")
    return configurations


def build_calibration_fit_configs(
    *, source_spec_dir: Path, dataset_version_ids: Mapping[str, str],
    data_ids_by_family: Mapping[str, Mapping[str, Any]],
    update_counts_by_family: Mapping[str, int],
    c3_overrides_by_family: Mapping[str, Mapping[str, Any]] | None = None,
    c3_labels_by_family: Mapping[str, Mapping[Any, int]] | None = None,
    mutation_seed: int = DEFAULT_MUTATION_SEED,
    calibration_seeds: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Build anchor, slowest, and explicit C3/C4 per-family calibration fits.

    C3 follows the explicit PCG64 mutation rule and ordered researcher IDs.
    An explicit C3 override remains available for a separately versioned
    selection; this helper never uses content hashes.
    """
    models_doc, tasks_doc = _load_source_documents(source_spec_dir)
    families = ["darcy", "bloodmnist", "etth1"]
    data_doc = {family: _resolve_data_ids(family, data_ids_by_family[family]) for family in families}
    seeds = calibration_seeds or {"darcy": 913711, "bloodmnist": 913712, "etth1": 913713}
    tasks_by_family: dict[str, list[Mapping[str, Any]]] = {family: [] for family in families}
    for task in tasks_doc["tasks"]:
        tasks_by_family[str(task["family_id"])].append(task)
    outputs: list[dict[str, Any]] = []
    for family in families:
        model = _family_model(models_doc, family)
        update_options = [int(x) for x in model["allowed_pre_freeze_update_counts"]]
        update_count = int(update_counts_by_family[family])
        if update_count not in update_options:
            raise ValueError(f"Calibration update count {update_count} is not allowed for {family}: {update_options}")
        first_task = tasks_by_family[family][0]
        anchor_recipe = _base_family_recipe(model)
        declared_anchor = next((candidate for candidate in first_task.get("snapshot_configurations", [])
                                if candidate.get("is_anchor")), None)
        if declared_anchor is not None:
            anchor_recipe.update(copy.deepcopy(dict(declared_anchor.get("overrides", {}))))
        long_task = next((t for t in tasks_by_family[family] if t.get("long_from_scratch")), None)
        if long_task is None:
            raise ValueError(f"No declared combined slowest-candidate space for {family}")
        long_axes = long_task["long_from_scratch"]["cartesian_axes"]
        slowest = {name: copy.deepcopy(values[-1]) for name, values in long_axes.items()}
        if c3_overrides_by_family is not None and family in c3_overrides_by_family:
            c3 = copy.deepcopy(dict(c3_overrides_by_family[family]))
            c3_metadata = {"method": "explicit_selection", "mutation_seed": int(mutation_seed)}
        else:
            c3, c3_metadata = derive_c3_revision(
                family, data_doc[family], mutation_seed=mutation_seed,
                class_labels=(c3_labels_by_family or {}).get(family),
            )
        if family in ("darcy", "bloodmnist"):
            if "train_sample_ids" not in c3:
                raise ValueError(f"C3 for {family} needs an explicit retained train_sample_ids list")
        elif "excluded_training_row_interval" not in c3:
            raise ValueError("ETTh1 C3 needs an explicit excluded_training_row_interval")
        c3 = {**copy.deepcopy(anchor_recipe), **c3}
        c4 = {**copy.deepcopy(anchor_recipe), **{"darcy": {"c4_gradient_mse_weight": 0.02},
              "bloodmnist": {"c4_weight_l2": 0.01},
              "etth1": {"c4_weight_l2": 0.01}}[family]}
        recipe_list = (
            ("anchor", anchor_recipe),
            ("slowest_candidate", slowest),
            ("C3_revision", c3),
            ("C4_revision", c4),
        )
        for role, overrides in recipe_list:
            ids = copy.deepcopy(data_doc[family])
            if role == "C3_revision" and family in ("darcy", "bloodmnist"):
                ids["train_sample_ids"] = list(map(int, overrides["train_sample_ids"]))
            fit_config = build_fit_config(
                source_spec_dir=source_spec_dir, task_id=str(first_task["task_id"]),
                configuration_id=f"{family}_{role}", seed=int(seeds[family]),
                optimizer_updates=update_count, dataset_version_id=dataset_version_ids[family],
                data_ids=ids, phase="calibration", configuration_overrides=overrides,
                calibration_role=role, allowed_update_counts=update_options,
            )
            fit_config["is_anchor"] = role in {"anchor", "C3_revision", "C4_revision"}
            fit_config["anchor_configuration_id"] = str(declared_anchor.get("configuration_id")) if declared_anchor else f"{family}_declared_anchor"
            if role == "C3_revision":
                fit_config["mutation_version_id"] = "C3-pcg64-20261012-v1"
                fit_config["mutation_selection"] = copy.deepcopy(c3_metadata)
                validate_config(fit_config)
            outputs.append(fit_config)
    return outputs


def derive_c3_revision(
    family: str, data_ids: Mapping[str, Any], *, mutation_seed: int = DEFAULT_MUTATION_SEED,
    class_labels: Mapping[Any, int] | None = None, temporal_row_count: int = 8640,
    removed_row_count: int = 432,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a deterministic five-percent C3 training-cohort revision.

    Sample IDs are consumed in their declared sequence. PCG64 chooses indices;
    retained IDs preserve the original sequence. ETTh1 uses one contiguous row
    interval and drops every origin whose maximal 192-step history plus
    24-step target overlaps that interval.
    """
    rng = np.random.Generator(np.random.PCG64(int(mutation_seed)))
    initial_state = copy.deepcopy(rng.bit_generator.state)
    if family in ("darcy", "bloodmnist"):
        key = "train_sample_ids"
        if key not in data_ids:
            raise ValueError(f"C3 for {family} needs explicit ordered train_sample_ids")
        ordered = list(data_ids[key])
        if not ordered or len(set(ordered)) != len(ordered):
            raise ValueError("C3 sample IDs must be non-empty and unique")
        if family == "darcy":
            n_remove = int(math.floor(len(ordered) * 0.05))
            removed_positions = set(map(int, rng.permutation(len(ordered))[:n_remove]))
            stratum_counts = None
        else:
            if class_labels is None:
                raise ValueError("BloodMNIST C3 needs explicit training labels keyed by stable sample ID")
            absent = [sample_id for sample_id in ordered if sample_id not in class_labels]
            if absent:
                raise ValueError(f"BloodMNIST C3 labels are missing IDs: {absent[:8]}")
            labels = np.asarray([int(class_labels[sample_id]) for sample_id in ordered], dtype=np.int64)
            if np.any(labels < 0) or np.any(labels > 7):
                raise ValueError("BloodMNIST C3 labels must use fixed class IDs 0..7")
            removed_positions: set[int] = set()
            stratum_counts = {}
            for class_id in range(8):
                positions = np.flatnonzero(labels == class_id)
                if len(positions) == 0:
                    raise ValueError(f"BloodMNIST C3 training cohort is missing class {class_id}")
                remove_count = min(int(math.floor(len(positions) * 0.05)), len(positions) - 1)
                selected = rng.permutation(positions)[:remove_count]
                removed_positions.update(map(int, selected))
                stratum_counts[str(class_id)] = {"before": int(len(positions)), "removed": remove_count,
                                                   "after": int(len(positions) - remove_count)}
        removed = [sample_id for position, sample_id in enumerate(ordered) if position in removed_positions]
        retained = [sample_id for position, sample_id in enumerate(ordered) if position not in removed_positions]
        metadata = {
            "algorithm": "numpy.PCG64", "numpy_version": np.__version__, "mutation_seed": int(mutation_seed),
            "input_order": "explicit ordered stable sample IDs", "initial_bit_generator_state": initial_state,
            "input_count": len(ordered), "removed_count": len(removed), "removed_sample_ids": removed,
            "retained_sample_ids": retained, "class_counts": stratum_counts,
        }
        return {key: retained}, metadata
    if family != "etth1":
        raise ValueError(f"Unsupported C3 family: {family}")
    if int(temporal_row_count) < 1 or int(removed_row_count) < 1 or removed_row_count > temporal_row_count:
        raise ValueError("ETTh1 C3 row counts are invalid")
    train_origins = list(map(int, data_ids.get("train_origins", [])))
    validation_origins = list(map(int, data_ids.get("validation_origins", [])))
    if not train_origins or not validation_origins:
        raise ValueError("ETTh1 C3 needs explicit ordered training and validation origins")
    start = int(rng.integers(0, temporal_row_count - removed_row_count + 1))
    stop = start + int(removed_row_count)
    eligible = [origin for origin in train_origins if not (origin - 192 < stop and origin + 24 > start)]
    if not eligible:
        raise ValueError("ETTh1 C3 interval removed every configured training origin")
    metadata = {
        "algorithm": "numpy.PCG64", "numpy_version": np.__version__, "mutation_seed": int(mutation_seed),
        "initial_bit_generator_state": initial_state, "temporal_row_count": int(temporal_row_count),
        "removed_row_interval_half_open": [start, stop], "removed_row_count": int(removed_row_count),
        "maximal_window": {"context": 192, "horizon": 24}, "retained_train_origins": eligible,
        "validation_origins_unchanged": validation_origins,
    }
    return {"excluded_training_row_interval": [start, stop], "train_origins": eligible}, metadata


def build_episode_fit_config(
    *, source_spec_dir: Path, task_id: str, configuration_id: str, seed: int,
    mutation: str, work_id: str, dataset_version_id: str,
    data_ids: Mapping[str, Any], class_labels: Mapping[Any, int] | None = None,
    optimizer_updates: int, mutation_seed: int = DEFAULT_MUTATION_SEED,
    recipe_version_id: str | None = None, recipe_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one full task-recipe fit for a paired revision episode.

    The original task candidate is resolved from the registered source task.
    C3 changes the actual training cohort and train-only preprocessing inputs;
    C4 adds its family-specific objective term. This is a config builder only.
    """
    if mutation not in {"C3", "C4"}:
        raise ValueError("Training-update episode fits currently support registered C3 or C4 revisions")
    _models, tasks_doc = _load_source_documents(source_spec_dir)
    task = next((item for item in tasks_doc["tasks"] if item["task_id"] == task_id), None)
    if task is None:
        raise ValueError(f"Unknown task ID: {task_id}")
    candidates = list(task.get("snapshot_configurations", []))
    if task.get("long_from_scratch"):
        candidates.extend(enumerate_long_configurations(source_spec_dir=source_spec_dir, task_id=task_id))
    candidate = next((item for item in candidates if item["configuration_id"] == configuration_id), None)
    if candidate is None:
        raise ValueError(f"Configuration {configuration_id} is not registered for {task_id}")
    family = str(task["family_id"])
    effective_ids = _resolve_data_ids(family, data_ids)
    overrides = copy.deepcopy(dict(candidate.get("overrides", {})))
    effective_recipe = copy.deepcopy(dict(recipe_overrides or {}))
    mutation_metadata = None
    if mutation == "C3":
        if family == "darcy":
            model = _family_model(_models, family)
            cohort_size = int(overrides.get("training_sample_count", model["default_recipe"]["training_sample_count"]))
            effective_ids["train_sample_ids"] = effective_ids["train_sample_ids"][:cohort_size]
        c3_ids, mutation_metadata = derive_c3_revision(
            family, effective_ids, mutation_seed=mutation_seed, class_labels=class_labels,
        )
        effective_ids.update(c3_ids)
        if family == "darcy":
            overrides["training_sample_count"] = len(effective_ids["train_sample_ids"])
        recipe_version_id = recipe_version_id or f"C3-pcg64-seed{mutation_seed}-v1"
    else:
        c4_settings = {
            "darcy": {"c4_gradient_mse_weight": 0.02},
            "bloodmnist": {"c4_weight_l2": 0.01},
            "etth1": {"c4_weight_l2": 0.01},
        }[family]
        overrides.update(c4_settings)
        recipe_version_id = recipe_version_id or "C4-registered-revision-v1"
    config = build_fit_config(
        source_spec_dir=source_spec_dir, task_id=task_id,
        configuration_id=configuration_id, seed=seed,
        optimizer_updates=optimizer_updates, dataset_version_id=dataset_version_id,
        data_ids=effective_ids, phase="formal", work_id=work_id,
        configuration_overrides=overrides,
        recipe_version_id=recipe_version_id, recipe_overrides=effective_recipe,
    )
    config["mutation_id"] = mutation
    if mutation_metadata is not None:
        config["mutation_seed"] = int(mutation_seed)
        config["mutation_selection"] = mutation_metadata
    config["is_anchor"] = bool(candidate.get("is_anchor", False))
    return config


def fit(config: Mapping[str, Any], data: PreparedData, output: Path, *, device: str = "cuda", resume: bool = False) -> dict[str, Any]:
    """Execute one real fit. External supervisor owns the time lease/timeout."""
    validate_config(config)
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")
    if device == "cpu" and not bool(config.get("allow_cpu_regression", False)):
        raise RuntimeError("CPU fitting is reserved for explicit synthetic code-regression configs")
    target_device = torch.device(device)
    if torch.cuda.is_available():
        torch.backends.cuda.matmul.allow_tf32 = bool(config.get("tf32", False))
        torch.backends.cudnn.allow_tf32 = bool(config.get("tf32", False))
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "effective_config.json"
    checkpoint_path = output / "checkpoint.pt"
    log_path = output / "training.jsonl"
    if resume:
        if not config_path.is_file() or not checkpoint_path.is_file():
            raise FileNotFoundError("Resume needs both the original effective config and checkpoint")
        old_config = json.loads(config_path.read_text(encoding="utf-8"))
        if old_config != dict(config):
            raise ValueError("Resume rejected: full effective config differs from the original fit")
    elif any(p.exists() for p in (config_path, checkpoint_path, output / "execution_receipt.json")):
        raise FileExistsError(f"Output already contains a fit; choose a new run directory or use --resume: {output}")
    seed_everything(int(config["seed"]))
    model = build_model(str(config["family"]), config).to(target_device)
    optimizer_cfg = dict(config.get("optimizer", {}))
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(optimizer_cfg.get("lr", 1e-3)),
                                  betas=tuple(optimizer_cfg.get("betas", (0.9, 0.999))),
                                  eps=float(optimizer_cfg.get("eps", 1e-8)),
                                  weight_decay=float(optimizer_cfg.get("weight_decay", 1e-4)))
    total_updates = int(config["optimizer_updates"])
    initial_lr = float(optimizer_cfg.get("lr", 1e-3))
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=total_updates,
        eta_min=initial_lr * float(config.get("scheduler", {}).get("final_lr_fraction", 0.1)),
    )
    x, y = data.x.to(target_device), data.y.to(target_device)
    completed, permutation, cursor = 0, torch.randperm(len(x), device=target_device), 0
    observations: list[dict[str, float]] = []
    elapsed_before = 0.0
    if resume:
        state = torch.load(checkpoint_path, map_location=target_device, weights_only=False)
        if state["effective_config"] != dict(config):
            raise ValueError("Checkpoint contract does not match effective config")
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        completed, permutation, cursor = int(state["completed_updates"]), state["sampler_permutation"], int(state["sampler_cursor"])
        observations, elapsed_before = list(state["observations"]), float(state["elapsed_training_seconds"])
        _restore_rng(state["rng"])
    else:
        atomic_json(config_path, dict(config))
    start = time.monotonic()
    model.train()
    with log_path.open("a" if resume else "w", encoding="utf-8", newline="\n") as log:
        while completed < total_updates:
            batch_size = int(config["batch_size"])
            if cursor + batch_size <= len(x):
                indices = permutation[cursor:cursor + batch_size]
                cursor += batch_size
            else:
                tail = permutation[cursor:]
                permutation = torch.randperm(len(x), device=target_device)
                needed = batch_size - len(tail)
                indices = torch.cat((tail, permutation[:needed]))
                cursor = needed
            xb, yb = x[indices], y[indices]
            if config["family"] == "bloodmnist" and config.get("augmentation", "none") in ("rot90", "rot90_hflip"):
                turns = torch.randint(0, 4, (len(xb),), device=target_device)
                xb = torch.stack([torch.rot90(image, int(turn), dims=(-2, -1)) for image, turn in zip(xb, turns)])
                if config.get("augmentation") == "rot90_hflip":
                    flips = torch.rand(len(xb), device=target_device) < 0.5
                    xb = torch.stack([image.flip(-1) if bool(flip) else image for image, flip in zip(xb, flips)])
            optimizer.zero_grad(set_to_none=True)
            prediction = model(xb)
            loss = _objective(str(config["family"]), model, prediction, yb, config, data.metadata)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"Non-finite objective at update {completed + 1}")
            loss.backward()
            optimizer.step()
            scheduler.step()
            completed += 1
            observations.append({"update": completed, "train_loss": float(loss.detach().cpu()), "learning_rate": float(optimizer.param_groups[0]["lr"])})
            if completed % int(config.get("log_every_updates", 25)) == 0 or completed == total_updates:
                elapsed = elapsed_before + time.monotonic() - start
                row = {**observations[-1], "elapsed_training_seconds": elapsed}
                log.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                log.flush(); os.fsync(log.fileno())
                atomic_torch_save(checkpoint_path, {
                    "effective_config": dict(config), "model": model.state_dict(), "optimizer": optimizer.state_dict(),
                    "scheduler": scheduler.state_dict(), "completed_updates": completed,
                    "sampler_permutation": permutation, "sampler_cursor": cursor,
                    "observations": observations, "elapsed_training_seconds": elapsed, "rng": _rng_state(),
                })
    fit_elapsed = elapsed_before + time.monotonic() - start
    metrics, predictions = evaluate(str(config["family"]), model, data, config, target_device)
    prediction_path = output / "validation_predictions.npz"
    sample_ids = data.metadata["validation_sample_ids"]
    atomic_npz(prediction_path, predictions=predictions, sample_ids=np.asarray(sample_ids, dtype=str))
    # A complete restart point is committed before the success receipt.
    atomic_torch_save(checkpoint_path, {
        "effective_config": dict(config), "model": model.state_dict(), "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(), "completed_updates": completed,
        "sampler_permutation": permutation, "sampler_cursor": cursor,
        "observations": observations, "elapsed_training_seconds": fit_elapsed, "rng": _rng_state(),
        "validation_metrics": metrics,
    })
    receipt = {
        "status": "SUCCEEDED", "work_id": config["work_id"], "phase": config["phase"],
        "formal_score_eligible": bool(config.get("formal_score_eligible", config["phase"] != "pilot")),
        "task_id": config["task_id"], "configuration_id": config["configuration_id"],
        "dataset_version_id": config["dataset_version_id"], "seed": int(config["seed"]),
        "completed_updates": completed, "optimizer_updates_required": total_updates,
        "training_seconds_inside_runtime": fit_elapsed,
        "device": str(target_device), "device_name": torch.cuda.get_device_name(target_device) if target_device.type == "cuda" else platform.processor(),
        "effective_config": dict(config), "data_manifest": data.metadata,
        "environment": {"python": sys.version, "torch": torch.__version__, "numpy": np.__version__, "platform": platform.platform()},
        "training_loss_first": observations[0]["train_loss"], "training_loss_last": observations[-1]["train_loss"],
        "validation_metrics": metrics, "validation_prediction_file": prediction_path.name,
        "hidden_test_opened": False, "receipt_note": "Supervisor lease timing is authoritative; runtime time excludes supervisor setup and queueing.",
    }
    atomic_json(output / "validation_metrics.json", {"phase": config["phase"], "formal_score_eligible": receipt["formal_score_eligible"], "metrics": metrics, "validation_sample_ids": sample_ids})
    atomic_json(output / "execution_receipt.json", receipt)
    return receipt


def _dev_config(task: str, campaign: Path, output: Path, *, work_id: str | None = None) -> tuple[dict[str, Any], PreparedData]:
    amendment_path = campaign / "protocol/amendments/dev_configs_v2.0.1.json"
    selection_path = campaign / "protocol/amendments/dev_selection_v2.0.1.json"
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    source = amendment["development_tasks"][task]
    family = {"D_DEV": "darcy", "M_DEV": "bloodmnist", "T_DEV": "etth1"}[task]
    config: dict[str, Any] = {
        "work_id": work_id or output.name, "phase": "pilot", "formal_score_eligible": False,
        "task_id": task, "configuration_id": task, "dataset_version_id": f"researcher-{family}-registered-v2.0.1",
        "recipe_version_id": "dev-amendment-v2.0.1",
        "family": family, "seed": int(source["seed"]), "optimizer_updates": int(source["optimizer_updates"]),
        "batch_size": int(source["batch_size"]), "effective_batch_size": int(source["batch_size"]),
        "optimizer": {"name": "AdamW", "lr": 0.001, "betas": [0.9, 0.999], "eps": 1e-8, "weight_decay": 1e-4},
        "scheduler": {"name": "cosine", "final_lr_fraction": 0.1}, "precision": "float32", "tf32": False,
        "augmentation": source.get("augmentation", "none"), "label_smoothing": float(source.get("label_smoothing", 0.0)),
        "class_weighting": "none", "log_every_updates": 25,
        "output_reference": str(output),
    }
    if task == "D_DEV":
        config.update(fourier_modes_per_axis=int(source["modes"]), hidden_width=int(source["width"]), spectral_layers=int(source["spectral_blocks"]),
                      gradient_loss_weight=float(source["gradient_loss_weight"]), training_sample_count=int(source["train_samples"]),
                      train_sample_ids=list(map(int, selection["samples"]["D"]["train"])),
                      validation_sample_ids=list(map(int, selection["samples"]["D"]["validation"])))
    elif task == "M_DEV":
        config.update(resolution=int(source["image_resolution"]), training_sample_count=int(source["train_samples"]), class_count=8,
                      train_sample_ids=list(map(int, selection["samples"]["M"]["train"])),
                      validation_sample_ids=list(map(int, selection["samples"]["M"]["validation"])))
    else:
        config.update(context_length=int(source["context_length"]), forecast_horizon=int(source["prediction_horizon"]),
                      feature_set="all_7", loss_name="huber", huber_delta=float(source["huber_delta"]), dropout=float(source["dropout"]),
                      train_origins=list(range(source["train_origins"]["start"], source["train_origins"]["stop_exclusive"], source["train_origins"]["step"])),
                      validation_origins=list(range(source["validation_origins"]["start"], source["validation_origins"]["stop_exclusive"], source["validation_origins"]["step"])))
    family_batch = int(source["batch_size"])
    config["batch_size"] = family_batch
    prepared = prepare_data(family, campaign / "data/researcher", config, dev_selection=selection)
    if task == "M_DEV":
        train_counts = np.bincount(prepared.y.numpy(), minlength=8)
        val_counts = np.bincount(prepared.validation_y.numpy(), minlength=8)
        if len(prepared.x) != 512 or len(prepared.validation_x) != 128 or not np.all(train_counts == 64) or not np.all(val_counts == 16):
            raise ValueError("Registered BloodMNIST DEV sample/class counts did not match")
    config["output_reference"] = str(output)
    return config, prepared


def _prepare_explicit_dev_config(config: Mapping[str, Any], campaign: Path) -> PreparedData:
    """Prepare a complete caller config against its exact registered DEV IDs."""
    selection_path = campaign / "protocol/amendments/dev_selection_v2.0.1.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    task = str(config.get("task_id", ""))
    key = {"D_DEV": "D", "M_DEV": "M", "T_DEV": "T"}.get(task)
    if key is None:
        raise ValueError("Explicit pilot config task_id must be D_DEV, M_DEV, or T_DEV")
    registered = selection["samples"][key]
    if key in ("D", "M"):
        for config_key, selection_key in (("train_sample_ids", "train"), ("validation_sample_ids", "validation")):
            if list(map(int, config.get(config_key, []))) != list(map(int, registered[selection_key])):
                raise ValueError(f"Explicit DEV config {config_key} differs from the registered DEV selection")
    else:
        amendment = json.loads((campaign / "protocol/amendments/dev_configs_v2.0.1.json").read_text(encoding="utf-8"))
        source = amendment["development_tasks"][task]
        expected_train = list(range(source["train_origins"]["start"], source["train_origins"]["stop_exclusive"], source["train_origins"]["step"]))
        expected_validation = list(range(source["validation_origins"]["start"], source["validation_origins"]["stop_exclusive"], source["validation_origins"]["step"]))
        if list(map(int, config.get("train_origins", []))) != expected_train:
            raise ValueError("Explicit ETTh1 DEV config train_origins differ from the registered DEV selection")
        if list(map(int, config.get("validation_origins", []))) != expected_validation:
            raise ValueError("Explicit ETTh1 DEV config validation_origins differ from the registered DEV selection")
    return prepare_data(str(config["family"]), campaign / "data/researcher", config, dev_selection=selection)


def cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-mode", choices=("snapshot", "calibration"), help="Emit prospective full fit configs without running a fit")
    parser.add_argument("--phase", choices=("pilot", "calibration", "formal"), default="pilot")
    parser.add_argument("--task", choices=("D_DEV", "M_DEV", "T_DEV"))
    parser.add_argument("--campaign", type=Path, required=True, help="Explicit campaign root; no personal path default is used")
    parser.add_argument("--source-spec-dir", type=Path, help="Defaults to campaign/frozen/source_spec")
    parser.add_argument("--config", type=Path, help="Full explicit JSON fit configuration (calibration/formal)")
    parser.add_argument("--plan-input", type=Path, help="Read one complete fit config from a prospective plan JSON")
    parser.add_argument("--plan-index", type=int, help="Zero-based config index within --plan-input")
    parser.add_argument("--output", type=Path, help="Independent directory for one fit")
    parser.add_argument("--work-id", help="Explicit supervisor work ID; defaults to the stable output directory name")
    parser.add_argument("--plan-output", type=Path, help="Output JSON path for --plan-mode")
    parser.add_argument("--data-selection-json", type=Path, help="Explicit per-family train/validation IDs")
    parser.add_argument("--use-researcher-split-ids", action="store_true", help="Build ordered formal IDs from the allowlisted researcher splits and declared ETTh1 intervals")
    parser.add_argument("--dataset-versions-json", type=Path, help="Explicit per-family dataset-version IDs")
    parser.add_argument("--update-counts-json", type=Path, help="Explicit per-family permitted update counts")
    parser.add_argument("--c3-overrides-json", type=Path, help="Optional explicit C3 IDs or ETTh1 excluded interval")
    parser.add_argument("--c3-labels-json", type=Path, help="Optional BloodMNIST training labels keyed by stable sample ID")
    parser.add_argument("--mutation-seed", type=int, default=DEFAULT_MUTATION_SEED)
    parser.add_argument("--calibration-seeds-json", type=Path, help="Optional explicit per-family calibration seeds")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--shape-check", action="store_true", help="Prepare allowed train/validation data and check model interfaces; performs no optimizer update")
    args = parser.parse_args(argv)
    if args.plan_mode:
        if args.plan_output is None or (args.data_selection_json is None and not args.use_researcher_split_ids) or args.dataset_versions_json is None or args.update_counts_json is None:
            parser.error("plan mode requires --plan-output, a data-selection JSON or --use-researcher-split-ids, --dataset-versions-json, and --update-counts-json")
        source_spec_dir = args.source_spec_dir or args.campaign / "frozen/source_spec"
        if args.data_selection_json:
            data_ids_by_family = json.loads(args.data_selection_json.read_text(encoding="utf-8"))
        else:
            data_ids_by_family = researcher_data_selection(args.campaign / "data/researcher")
        common = {
            "source_spec_dir": source_spec_dir,
            "dataset_version_ids": json.loads(args.dataset_versions_json.read_text(encoding="utf-8")),
            "data_ids_by_family": data_ids_by_family,
            "update_counts_by_family": json.loads(args.update_counts_json.read_text(encoding="utf-8")),
        }
        if args.plan_mode == "snapshot":
            configs = build_snapshot_fit_configs(**common)
        else:
            extra = {"mutation_seed": args.mutation_seed}
            if args.c3_overrides_json:
                extra["c3_overrides_by_family"] = json.loads(args.c3_overrides_json.read_text(encoding="utf-8"))
            if args.c3_labels_json:
                labels = json.loads(args.c3_labels_json.read_text(encoding="utf-8"))
                extra["c3_labels_by_family"] = {family: {int(k): int(v) for k, v in values.items()}
                                                  for family, values in labels.items()}
            elif "bloodmnist" in data_ids_by_family:
                extra["c3_labels_by_family"] = {"bloodmnist": researcher_c3_labels(args.campaign / "data/researcher")}
            if args.calibration_seeds_json:
                extra["calibration_seeds"] = json.loads(args.calibration_seeds_json.read_text(encoding="utf-8"))
            configs = build_calibration_fit_configs(**common, **extra)
        bundle = {"plan_mode": args.plan_mode, "fit_count": len(configs), "configs": configs,
                  "note": "Prospective explicit fit configurations; no model fit or outcome was produced."}
        atomic_json(args.plan_output, bundle)
        print(json.dumps({"plan_mode": args.plan_mode, "fit_count": len(configs), "plan_output": str(args.plan_output)}, sort_keys=True))
        return 0
    if args.output is None:
        parser.error("single-fit mode requires --output")
    if args.plan_input is not None:
        if args.config is not None or args.task is not None or args.plan_index is None:
            parser.error("--plan-input requires --plan-index and cannot be combined with --config or --task")
        bundle = json.loads(args.plan_input.read_text(encoding="utf-8"))
        try:
            config = bundle["configs"][args.plan_index]
        except (KeyError, IndexError, TypeError):
            parser.error("--plan-index does not identify a fit config")
        if config.get("phase") != args.phase:
            parser.error("selected config phase does not match --phase")
        data = prepare_data(str(config["family"]), args.campaign / "data/researcher", config)
    elif args.phase == "pilot" and args.config is not None:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if config.get("phase") != "pilot" or config.get("formal_score_eligible") is not False:
            parser.error("Explicit DEV config must use phase='pilot' and formal_score_eligible=false")
        validate_config(config)
        data = _prepare_explicit_dev_config(config, args.campaign)
    elif args.phase == "pilot":
        if args.task is None:
            parser.error("--task is required for the pilot phase")
        config, data = _dev_config(args.task, args.campaign, args.output, work_id=args.work_id)
    else:
        if args.config is None:
            parser.error("--config is required for calibration/formal phase")
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if config.get("phase") != args.phase:
            parser.error("configuration phase does not match --phase")
        data = prepare_data(str(config["family"]), args.campaign / "data/researcher", config)
    if args.shape_check:
        validate_config(config)
        model = build_model(str(config["family"]), config).cpu().eval()
        with torch.inference_mode():
            result = model(data.x[: min(2, len(data.x))].cpu())
        expected_tail = {"darcy": (1, 64, 64), "bloodmnist": (8,), "etth1": (int(config.get("forecast_horizon", 24)),)}[config["family"]]
        if tuple(result.shape[1:]) != expected_tail:
            raise RuntimeError(f"Model output {tuple(result.shape)} does not match expected tail {expected_tail}")
        print(json.dumps({"shape_check": "PASS", "phase": args.phase, "task_id": config["task_id"],
                          "train_shape": list(data.x.shape), "validation_shape": list(data.validation_x.shape),
                          "model_output_shape": list(result.shape), "optimizer_updates": 0,
                          "formal_score_eligible": config.get("formal_score_eligible", args.phase != "pilot")}, sort_keys=True))
        return 0
    if args.device == "cpu" and config.get("phase") != "runtime_regression":
        raise RuntimeError("CPU fits are restricted to synthetic runtime regression")
    receipt = fit(config, data, args.output, device=args.device, resume=args.resume)
    print(json.dumps({"status": receipt["status"], "work_id": receipt["work_id"], "task_id": receipt["task_id"],
                      "completed_updates": receipt["completed_updates"], "validation_metrics": receipt["validation_metrics"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
