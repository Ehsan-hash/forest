from __future__ import annotations

import json

import numpy as np
import pytest
torch = pytest.importorskip("torch")
import research.experiments.training_runtime as runtime

from research.experiments.training_runtime import (
    PreparedData,
    TinyCNN6,
    WindowMLP,
    _resize_image_tensor,
    build_model,
    cli,
    build_calibration_fit_configs,
    build_episode_fit_config,
    derive_c3_revision,
    build_snapshot_fit_configs,
    enumerate_long_configurations,
    fit,
    prepare_data,
    score_predictions,
    validate_calibration_bundle,
    validate_config,
)


def test_architecture_output_contracts() -> None:
    fno = build_model("darcy", {"fourier_modes_per_axis": 4, "hidden_width": 8, "spectral_layers": 3}).eval()
    cnn = TinyCNN6().eval()
    mlp = WindowMLP(context=96, features=7, dropout=0.1).eval()
    with torch.inference_mode():
        assert fno(torch.zeros(1, 3, 64, 64)).shape == (1, 1, 64, 64)
        assert cnn(torch.zeros(1, 3, 48, 48)).shape == (1, 8)
        assert mlp(torch.zeros(1, 96, 7)).shape == (1, 24)


def test_float_image_noise_is_not_quantized_before_resize() -> None:
    image = torch.full((1, 3, 64, 64), 0.501, dtype=torch.float32)
    resized = _resize_image_tensor(image, 48)
    torch.testing.assert_close(resized, image[:, :, :48, :48])


def test_prepare_etth1_dev_uses_registered_bounds_and_scaler(tmp_path) -> None:
    # Synthetic arrays verify the parser's bounds without consulting any
    # hidden/evaluator data or producing scientific measurements.
    root = tmp_path
    values = np.arange(8640 * 7, dtype=np.float64).reshape(8640, 7)
    val = np.arange(2880 * 7, dtype=np.float64).reshape(2880, 7) + values[-1, -1] + 1
    np.savez(root / "etth1_train_validation.npz", columns=np.asarray(["HUFL", "HULL", "MUFL", "MULL", "LUFL", "LULL", "OT"]), train_values=values, validation_values=val)
    selection = {"samples": {"T": {"scaler": {"fit_history_rows_half_open": [96, 2192], "row_count": 2096,
        "mean": [1.0] * 7, "std_population": [2.0] * 7}}}}
    config = {"context_length": 96, "forecast_horizon": 24, "feature_set": "all_7", "loss_name": "huber", "huber_delta": 1.0,
              "train_origins": list(range(192, 2208, 16)), "validation_origins": list(range(2304, 2816, 16))}
    data = prepare_data("etth1", root, config, dev_selection=selection)
    assert data.x.shape == (126, 96, 7)
    assert data.validation_x.shape == (32, 96, 7)
    assert data.metadata["normalization_row_ids"] == list(range(96, 2192))


def test_etth1_c3_gap_and_c2_noise_are_consistent_across_contexts(tmp_path) -> None:
    root = tmp_path
    rng = np.random.default_rng(4)
    values = rng.normal(size=(8640, 7))
    validation = rng.normal(size=(2880, 7))
    np.savez(root / "etth1_train_validation.npz", columns=np.asarray(["HUFL", "HULL", "MUFL", "MULL", "LUFL", "LULL", "OT"]), train_values=values, validation_values=validation)
    common = {"dataset_version_id": "synthetic-etth-v1", "train_origins": [2900, 3000, 3400, 3600, 3650],
              "validation_origins": [8640], "excluded_training_row_interval": [3000, 3432],
              "validation_noise_std_in_training_sd": 0.02, "validation_noise_seed": 991}
    short = prepare_data("etth1", root, {**common, "context_length": 48, "feature_set": "high_load_plus_ot"})
    long = prepare_data("etth1", root, {**common, "context_length": 96, "feature_set": "all_7"})
    clean = prepare_data("etth1", root, {**common, "context_length": 48, "feature_set": "high_load_plus_ot", "validation_noise_std_in_training_sd": 0.0})
    long_clean = prepare_data("etth1", root, {**common, "context_length": 96, "feature_set": "all_7", "validation_noise_std_in_training_sd": 0.0})
    assert short.metadata["train_origins"] == [2900, 3650]
    assert not (set(range(3000, 3432)) & set(short.metadata["normalization_row_ids"]))
    # Both candidates receive identical noise for the same raw feature/time.
    torch.testing.assert_close(short.validation_x[0, -1, 0] - clean.validation_x[0, -1, 0],
                               long.validation_x[0, -1, 0] - long_clean.validation_x[0, -1, 0],
                               atol=1e-6, rtol=1e-6)


def test_runtime_regression_fit_writes_restartable_artifacts(tmp_path) -> None:
    torch.manual_seed(19)
    x = torch.randn(5, 8, 7)
    y = torch.randn(5, 3)
    validation_x = torch.randn(4, 8, 7)
    validation_y = torch.randn(4, 3)
    data = PreparedData(x, y, validation_x, validation_y, {
        "dataset_version_id": "synthetic-runtime-regression-v1",
        "train_sample_ids": [f"train:{i}" for i in range(5)],
        "validation_sample_ids": [f"validation:{i}" for i in range(4)],
        "normalization_mean": [0.0] * 7,
        "normalization_std_population": [1.0] * 7,
    })
    config = {
        "work_id": "runtime-regression", "phase": "runtime_regression", "formal_score_eligible": False,
        "task_id": "synthetic-T", "configuration_id": "synthetic-c0", "dataset_version_id": "synthetic-runtime-regression-v1",
        "family": "etth1", "seed": 7, "optimizer_updates": 2, "batch_size": 4, "effective_batch_size": 4,
        "context_length": 8, "feature_set": "all_7", "forecast_horizon": 3, "dropout": 0.1, "loss_name": "mse",
        "allow_cpu_regression": True,
        "log_every_updates": 1, "optimizer": {"name": "AdamW", "lr": 0.001, "betas": [0.9, 0.999], "eps": 1e-8, "weight_decay": 1e-4},
    }
    out = tmp_path / "run"
    receipt = fit(config, data, out, device="cpu")
    assert receipt["completed_updates"] == 2
    assert receipt["hidden_test_opened"] is False
    assert (out / "checkpoint.pt").is_file()
    saved = json.loads((out / "effective_config.json").read_text())
    assert saved == config
    assert json.loads((out / "execution_receipt.json").read_text())["validation_metrics"] == receipt["validation_metrics"]
    with np.load(out / "validation_predictions.npz", allow_pickle=False) as archive:
        re_scored = score_predictions("etth1", archive["predictions"], data)
        assert archive["sample_ids"].tolist() == data.metadata["validation_sample_ids"]
    assert re_scored == receipt["validation_metrics"]
    # Completed checkpoint can be reopened and revalidated without any new update.
    resumed = fit(config, data, out, device="cpu", resume=True)
    assert resumed["completed_updates"] == 2
    with pytest.raises(ValueError, match="full effective config differs"):
        fit({**config, "seed": 8}, data, out, device="cpu", resume=True)


def test_partial_checkpoint_resumes_without_restarting_updates(tmp_path, monkeypatch) -> None:
    data = PreparedData(torch.randn(6, 8, 7), torch.randn(6, 3), torch.randn(3, 8, 7), torch.randn(3, 3), {
        "dataset_version_id": "synthetic-resume-v1", "train_sample_ids": [str(i) for i in range(6)],
        "validation_sample_ids": [f"v{i}" for i in range(3)],
        "normalization_mean": [0.0] * 7, "normalization_std_population": [1.0] * 7,
    })
    config = {
        "work_id": "synthetic-resume", "phase": "runtime_regression", "formal_score_eligible": False,
        "task_id": "synthetic-T", "configuration_id": "synthetic-c0", "dataset_version_id": "synthetic-resume-v1",
        "family": "etth1", "seed": 31, "optimizer_updates": 3, "batch_size": 3, "effective_batch_size": 3,
        "context_length": 8, "feature_set": "all_7", "forecast_horizon": 3, "dropout": 0.0, "loss_name": "mse",
        "allow_cpu_regression": True,
        "log_every_updates": 1, "optimizer": {"name": "AdamW", "lr": 0.001, "betas": [0.9, 0.999], "eps": 1e-8, "weight_decay": 1e-4},
    }
    original_objective = runtime._objective
    calls = 0

    def fail_after_saved_step(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated interruption after durable update one")
        return original_objective(*args, **kwargs)

    out = tmp_path / "partial"
    monkeypatch.setattr(runtime, "_objective", fail_after_saved_step)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        fit(config, data, out, device="cpu")
    checkpoint = torch.load(out / "checkpoint.pt", map_location="cpu", weights_only=False)
    assert checkpoint["completed_updates"] == 1
    monkeypatch.setattr(runtime, "_objective", original_objective)
    receipt = fit(config, data, out, device="cpu", resume=True)
    assert receipt["completed_updates"] == 3


def test_one_update_cuda_runtime_regression_is_synthetic_only(tmp_path) -> None:
    if not torch.cuda.is_available():
        pytest.skip("CUDA is unavailable")
    torch.manual_seed(23)
    data = PreparedData(torch.randn(4, 8, 7), torch.randn(4, 3), torch.randn(3, 8, 7), torch.randn(3, 3), {
        "dataset_version_id": "synthetic-cuda-regression-v1",
        "train_sample_ids": [f"train:{i}" for i in range(4)],
        "validation_sample_ids": [f"validation:{i}" for i in range(3)],
        "normalization_mean": [0.0] * 7, "normalization_std_population": [1.0] * 7,
    })
    config = {
        "work_id": "synthetic-cuda-regression", "phase": "runtime_regression", "formal_score_eligible": False,
        "task_id": "synthetic-T", "configuration_id": "synthetic-c0", "dataset_version_id": "synthetic-cuda-regression-v1",
        "family": "etth1", "seed": 23, "optimizer_updates": 1, "batch_size": 4, "effective_batch_size": 4,
        "context_length": 8, "feature_set": "all_7", "forecast_horizon": 3, "dropout": 0.0, "loss_name": "mse",
        "log_every_updates": 1, "optimizer": {"name": "AdamW", "lr": 0.001, "betas": [0.9, 0.999], "eps": 1e-8, "weight_decay": 1e-4},
    }
    receipt = fit(config, data, tmp_path / "cuda-run", device="cuda")
    assert receipt["status"] == "SUCCEEDED"
    assert receipt["device"] == "cuda"
    assert receipt["completed_updates"] == 1
    assert receipt["hidden_test_opened"] is False


def test_pilot_requires_noneligible_marker() -> None:
    with pytest.raises(ValueError, match="formal_score_eligible=false"):
        validate_config({"work_id": "w", "phase": "pilot", "formal_score_eligible": True,
                         "task_id": "D_DEV", "configuration_id": "D_DEV", "dataset_version_id": "d-v1",
                         "family": "darcy", "seed": 1, "optimizer_updates": 1, "batch_size": 1,
                         "train_sample_ids": [1], "validation_sample_ids": [2]})


@pytest.mark.parametrize("phase", ["pilot", "calibration", "runtime_regression"])
@pytest.mark.parametrize("marker", [True, None])
def test_development_and_calibration_cannot_become_formal_results(phase, marker) -> None:
    config = {"work_id": "development", "phase": phase,
              "task_id": "D_DEV", "configuration_id": "D_DEV", "dataset_version_id": "synthetic",
              "family": "darcy", "seed": 1, "optimizer_updates": 1, "batch_size": 1}
    if marker is not None:
        config["formal_score_eligible"] = marker
    with pytest.raises(ValueError, match="formal_score_eligible=false"):
        validate_config(config)


@pytest.mark.parametrize("tf32", [False, True])
def test_actual_fit_preserves_backend_recipe_and_full_batches(tmp_path, monkeypatch, tf32) -> None:
    data = PreparedData(torch.randn(5, 8, 7), torch.randn(5, 3), torch.randn(2, 8, 7), torch.randn(2, 3), {
        "train_sample_ids": [str(i) for i in range(5)], "validation_sample_ids": ["v0", "v1"],
        "normalization_mean": [0.0] * 7, "normalization_std_population": [1.0] * 7})
    config = {"work_id": "small-cohort", "phase": "runtime_regression", "formal_score_eligible": False,
              "task_id": "synthetic-T", "configuration_id": "synthetic-c0", "dataset_version_id": "synthetic-v1",
              "family": "etth1", "seed": 31, "optimizer_updates": 2, "batch_size": 12, "effective_batch_size": 12,
              "context_length": 8, "feature_set": "all_7", "forecast_horizon": 3, "dropout": 0.0,
              "loss_name": "mse", "allow_cpu_regression": True, "tf32": tf32, "log_every_updates": 1}
    original = runtime._objective
    observations = []
    def observed_objective(family, model, prediction, target, *args):
        observations.append((len(target), torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32))
        return original(family, model, prediction, target, *args)
    monkeypatch.setattr(runtime, "_objective", observed_objective)
    initial_flags = (torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32)
    try:
        receipt = fit(config, data, tmp_path / "small", device="cpu")
        assert observations == [(12, tf32, tf32), (12, tf32, tf32)]
        assert receipt["effective_config"] == config
        checkpoint = torch.load(tmp_path / "small/checkpoint.pt", map_location="cpu", weights_only=False)
        assert 0 <= checkpoint["sampler_cursor"] <= len(data.x)
        # Restoring a completed fit keeps its exact sampler/RNG and adds no update.
        fit(config, data, tmp_path / "small", device="cpu", resume=True)
        assert len(observations) == 2
    finally:
        torch.backends.cuda.matmul.allow_tf32, torch.backends.cudnn.allow_tf32 = initial_flags


def test_partial_cuda_checkpoint_restores_cpu_and_cuda_rng(tmp_path, monkeypatch) -> None:
    if not torch.cuda.is_available():
        pytest.skip("CUDA is unavailable")
    data = PreparedData(torch.randn(5, 8, 7), torch.randn(5, 3), torch.randn(2, 8, 7), torch.randn(2, 3), {
        "train_sample_ids": [str(i) for i in range(5)], "validation_sample_ids": ["v0", "v1"],
        "normalization_mean": [0.0] * 7, "normalization_std_population": [1.0] * 7})
    config = {"work_id": "cuda-resume-regression", "phase": "runtime_regression", "formal_score_eligible": False,
              "task_id": "synthetic-T", "configuration_id": "synthetic-c0", "dataset_version_id": "synthetic-v1",
              "family": "etth1", "seed": 31, "optimizer_updates": 2, "batch_size": 12,
              "context_length": 8, "feature_set": "all_7", "forecast_horizon": 3, "dropout": 0.1,
              "loss_name": "mse", "log_every_updates": 1, "tf32": False}
    original, calls = runtime._objective, 0
    def interrupted_objective(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interruption after actual CUDA update one")
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime, "_objective", interrupted_objective)
    out = tmp_path / "interrupted"
    with pytest.raises(RuntimeError, match="actual CUDA update"):
        fit(config, data, out, device="cuda")
    saved = torch.load(out / "checkpoint.pt", map_location="cuda", weights_only=False)
    assert saved["completed_updates"] == 1
    assert saved["rng"]["torch_cpu"].is_cuda
    monkeypatch.setattr(runtime, "_objective", original)
    resumed = fit(config, data, out, device="cuda", resume=True)
    uninterrupted = fit(config, data, tmp_path / "uninterrupted", device="cuda")
    assert resumed["completed_updates"] == uninterrupted["completed_updates"] == 2
    assert resumed["formal_score_eligible"] is False
    a = torch.load(out / "checkpoint.pt", map_location="cpu", weights_only=False)
    b = torch.load(tmp_path / "uninterrupted/checkpoint.pt", map_location="cpu", weights_only=False)
    assert a["observations"] == b["observations"]
    assert a["sampler_cursor"] == b["sampler_cursor"]
    torch.testing.assert_close(a["sampler_permutation"], b["sampler_permutation"], rtol=0, atol=0)
    for key in a["model"]:
        torch.testing.assert_close(a["model"][key], b["model"][key], rtol=0, atol=0)


def test_calibration_bundle_requires_explicit_anchor_slowest_and_revisions() -> None:
    required = ("anchor", "slowest_candidate", "C3_revision", "C4_revision")
    configs = [{
        "work_id": f"cal-{role}", "phase": "calibration", "formal_score_eligible": False,
        "task_id": f"T-{role}", "configuration_id": role, "dataset_version_id": "etth-v1",
        "recipe_version_id": "test-recipe-v1",
        "family": "etth1", "seed": 913713, "optimizer_updates": 200, "batch_size": 64,
        "train_origins": [192], "validation_origins": [8640],
        "allowed_update_counts": [200, 400, 600], "calibration_role": role,
        "optimizer": {"name": "AdamW", "lr": 0.001, "betas": [0.9, 0.999], "eps": 1e-8, "weight_decay": 1e-4},
        "scheduler": {"name": "cosine", "final_lr_fraction": 0.1}, "precision": "float32", "tf32": False,
        "log_every_updates": 25,
    } for role in required]
    validate_calibration_bundle(configs, {"etth1": required})
    with pytest.raises(ValueError, match="missing etth1 roles"):
        validate_calibration_bundle(configs[:-1], {"etth1": required})
    with pytest.raises(ValueError, match="explicit allowed_update_counts"):
        validate_calibration_bundle([{**configs[0], "optimizer_updates": 300}], {"etth1": ("anchor",)})


def _write_builder_spec(root):
    source = root / "source_spec"
    source.mkdir()
    models = {"models": [
        {"model_id": "fno", "family_id": "darcy", "architecture": {"fourier_modes_per_axis": 8, "hidden_width": 24, "spectral_layers": 3},
         "default_recipe": {"optimizer_updates": 400, "effective_batch_size": 16, "training_sample_count": 2, "gradient_loss_weight": 0.0},
         "allowed_pre_freeze_update_counts": [200, 400, 600]},
        {"model_id": "cnn", "family_id": "bloodmnist", "architecture": {},
         "default_recipe": {"optimizer_updates": 600, "effective_batch_size": 128, "resolution": 64, "augmentation": "none", "label_smoothing": 0.0, "class_weighting": "none"},
         "allowed_pre_freeze_update_counts": [200, 400, 600, 800]},
        {"model_id": "mlp", "family_id": "etth1", "architecture": {},
         "default_recipe": {"optimizer_updates": 400, "effective_batch_size": 64, "context_length": 96, "feature_set": "all_7", "forecast_horizon": 24, "loss_name": "mse", "huber_delta": None, "dropout": 0.0},
         "allowed_pre_freeze_update_counts": [200, 400, 600]},
    ]}
    (source / "models.json").write_text(json.dumps(models))
    family_axis = {
        "darcy": {"fourier_modes_per_axis": [4, 8, 16], "hidden_width": [16, 24, 32]},
        "bloodmnist": {"augmentation": ["none", "rot90", "rot90_hflip"], "class_weighting": ["none", "inverse_sqrt_frequency", "inverse_frequency"]},
        "etth1": {"context_length": [48, 96, 192], "feature_set": ["ot_only", "high_load_plus_ot", "all_7"]},
    }
    family_anchor = {"darcy": {"fourier_modes_per_axis": 8, "hidden_width": 24},
                     "bloodmnist": {"augmentation": "none", "class_weighting": "none"},
                     "etth1": {"context_length": 96, "feature_set": "all_7"}}
    tasks = []
    for family, prefix in (("darcy", "D"), ("bloodmnist", "M"), ("etth1", "T")):
        for number in range(1, 5):
            long = None
            if number == 1:
                axes = family_axis[family]
                long = {"cartesian_axes": axes, "configuration_count": 9, "anchor_overrides": family_anchor[family]}
            tasks.append({"task_id": f"{prefix}0{number}", "family_id": family,
                          "scientific_question": "test question", "primary_metric": "metric", "primary_direction": "minimize",
                          "effect_threshold": {"kind": "relative_reduction", "value": 0.05}, "side_constraints": [],
                          "snapshot_configurations": [{"configuration_id": f"{prefix}0{number}_c{i}", "overrides": {"candidate": i}} for i in range(3)],
                          "long_from_scratch": long})
    (source / "tasks.json").write_text(json.dumps({"tasks": tasks}))
    return source


def test_complete_config_builder_covers_12_tasks_72_snapshot_fits_and_nine_spaces(tmp_path) -> None:
    source = _write_builder_spec(tmp_path)
    selections = {
        "darcy": {"train_sample_ids": [1, 2], "validation_sample_ids": [3]},
        "bloodmnist": {"train_sample_ids": list(range(10, 90)), "validation_sample_ids": [100]},
        "etth1": {"train_origins": [192], "validation_origins": [8640]},
    }
    versions = {family: f"{family}-data-v1" for family in selections}
    updates = {"darcy": 200, "bloodmnist": 400, "etth1": 600}
    configs = build_snapshot_fit_configs(source_spec_dir=source, dataset_version_ids=versions,
                                         data_ids_by_family=selections, update_counts_by_family=updates)
    assert len(configs) == 72
    assert len({config["work_id"] for config in configs}) == 72
    assert configs[0]["formal_score_eligible"] is True
    assert configs[0]["optimizer"]["name"] == "AdamW"
    assert configs[0]["dataset_version_id"] == "darcy-data-v1"
    for task_id in ("D01", "M01", "T01"):
        assert len(enumerate_long_configurations(source_spec_dir=source, task_id=task_id)) == 9


def test_calibration_builder_uses_deterministic_c3_and_emits_four_roles_per_family(tmp_path) -> None:
    source = _write_builder_spec(tmp_path)
    selections = {
        "darcy": {"train_sample_ids": [1, 2], "validation_sample_ids": [3]},
        "bloodmnist": {"train_sample_ids": list(range(10, 90)), "validation_sample_ids": [100]},
        "etth1": {"train_origins": list(range(192, 8617)), "validation_origins": [8640]},
    }
    versions = {family: f"{family}-data-v1" for family in selections}
    updates = {"darcy": 200, "bloodmnist": 400, "etth1": 600}
    labels = {"bloodmnist": {sample_id: (sample_id - 10) // 10 for sample_id in range(10, 90)}}
    configs = build_calibration_fit_configs(source_spec_dir=source, dataset_version_ids=versions,
                                            data_ids_by_family=selections, update_counts_by_family=updates,
                                            c3_labels_by_family=labels)
    assert len(configs) == 12
    assert all(config["phase"] == "calibration" and config["formal_score_eligible"] is False for config in configs)
    assert all(config["seed"] == {"darcy": 913711, "bloodmnist": 913712, "etth1": 913713}[config["family"]] for config in configs)
    validate_calibration_bundle(configs, {family: ["anchor", "slowest_candidate", "C3_revision", "C4_revision"] for family in selections})
    assert next(c for c in configs if c["calibration_role"] == "C4_revision" and c["family"] == "darcy")["c4_gradient_mse_weight"] == 0.02
    assert all(c["formal_score_eligible"] is False for c in configs)


def test_calibration_c3_removes_only_the_actual_anchor_cohort_without_replacement(tmp_path) -> None:
    source = _write_builder_spec(tmp_path)
    models = json.loads((source / "models.json").read_text())
    models["models"][0]["default_recipe"]["training_sample_count"] = 100
    (source / "models.json").write_text(json.dumps(models))
    selections = {"darcy": {"train_sample_ids": list(range(200)), "validation_sample_ids": [1000]},
                  "bloodmnist": {"train_sample_ids": list(range(2000, 2080)), "validation_sample_ids": [3000]},
                  "etth1": {"train_origins": list(range(192, 8617)), "validation_origins": [8640]}}
    configs = build_calibration_fit_configs(source_spec_dir=source,
        dataset_version_ids={family: "synthetic-" + family for family in selections},
        data_ids_by_family=selections, update_counts_by_family={family: 200 for family in selections},
        c3_labels_by_family={"bloodmnist": {i: (i - 2000) // 10 for i in range(2000, 2080)}})
    anchor = next(c for c in configs if c["family"] == "darcy" and c["calibration_role"] == "anchor")
    changed = next(c for c in configs if c["family"] == "darcy" and c["calibration_role"] == "C3_revision")
    root = tmp_path / "researcher"
    root.mkdir()
    rng = np.random.default_rng(1)
    np.savez(root / "darcy64_train.npz", sample_id=np.arange(200), x=rng.normal(size=(200, 4, 4)), y=rng.normal(size=(200, 4, 4)))
    np.savez(root / "darcy64_validation.npz", sample_id=np.asarray([1000]), x=rng.normal(size=(1, 4, 4)), y=rng.normal(size=(1, 4, 4)))
    anchor_data, changed_data = prepare_data("darcy", root, anchor), prepare_data("darcy", root, changed)
    assert anchor_data.metadata["train_sample_ids"] == list(range(100))
    assert len(changed_data.x) == changed["training_sample_count"] == 95
    assert set(changed_data.metadata["train_sample_ids"]) < set(anchor_data.metadata["train_sample_ids"])
    assert len(changed["mutation_selection"]["removed_sample_ids"]) == 5


def test_pcg64_c3_selection_is_deterministic_stratified_and_keeps_temporal_gap() -> None:
    ids = list(range(100))
    selection_a, metadata_a = derive_c3_revision("darcy", {"train_sample_ids": ids})
    selection_b, metadata_b = derive_c3_revision("darcy", {"train_sample_ids": ids})
    assert selection_a == selection_b
    assert metadata_a == metadata_b
    assert len(metadata_a["removed_sample_ids"]) == 5
    assert selection_a["train_sample_ids"] == [item for item in ids if item not in metadata_a["removed_sample_ids"]]

    blood_ids = list(range(240))
    labels = {sample_id: sample_id // 30 for sample_id in blood_ids}
    blood, blood_meta = derive_c3_revision("bloodmnist", {"train_sample_ids": blood_ids}, class_labels=labels)
    assert blood_meta["removed_count"] == 8
    assert blood_meta["class_counts"]["0"] == {"before": 30, "removed": 1, "after": 29}

    train_origins = list(range(192, 8617))
    temporal, temporal_meta = derive_c3_revision("etth1", {
        "train_origins": train_origins, "validation_origins": [8640, 8641],
    })
    start, stop = temporal["excluded_training_row_interval"]
    assert stop - start == 432
    assert temporal_meta["removed_row_interval_half_open"] == [start, stop]
    assert all(not (origin - 192 < stop and origin + 24 > start) for origin in temporal["train_origins"])
    assert temporal_meta["validation_origins_unchanged"] == [8640, 8641]


def test_scientific_recipe_can_change_when_explicitly_versioned(tmp_path) -> None:
    source = _write_builder_spec(tmp_path)
    selection = {"train_origins": [192, 208], "validation_origins": [8640]}
    config = runtime.build_fit_config(
        source_spec_dir=source, task_id="T01", configuration_id="T01_c0", seed=1001,
        optimizer_updates=200, dataset_version_id="etth-v1", data_ids=selection,
        phase="formal", recipe_version_id="lr-experiment-v2",
        recipe_overrides={
            "optimizer": {"name": "AdamW", "lr": 0.0005, "betas": [0.9, 0.999], "eps": 1e-8, "weight_decay": 0.01},
            "scheduler": {"name": "cosine", "final_lr_fraction": 0.2},
            "tf32": True,
        },
    )
    assert config["optimizer"]["lr"] == 0.0005
    assert config["scheduler"]["final_lr_fraction"] == 0.2
    assert config["tf32"] is True
    with pytest.raises(ValueError, match="recipe_version_id"):
        runtime.build_fit_config(
            source_spec_dir=source, task_id="T01", configuration_id="T01_c0", seed=1001,
            optimizer_updates=200, dataset_version_id="etth-v1", data_ids=selection,
            phase="formal", recipe_overrides={"optimizer": {"name": "AdamW", "lr": 0.0005,
                "betas": [0.9, 0.999], "eps": 1e-8, "weight_decay": 0.01}},
        )


def test_episode_builder_resolves_original_task_candidate_and_training_mutation(tmp_path) -> None:
    source = _write_builder_spec(tmp_path)
    data_ids = {"train_origins": list(range(192, 8617)), "validation_origins": list(range(8640, 8672))}
    config = build_episode_fit_config(
        source_spec_dir=source, task_id="T01", configuration_id="T01_c1", seed=1001,
        mutation="C3", work_id="live-T01-C3-seed1001", dataset_version_id="etth-v1",
        data_ids=data_ids, optimizer_updates=200,
    )
    assert config["configuration_overrides"]["candidate"] == 1
    assert config["mutation_id"] == "C3"
    assert config["formal_score_eligible"] is True
    assert config["mutation_selection"]["algorithm"] == "numpy.PCG64"
    start, stop = config["excluded_training_row_interval"]
    assert stop - start == 432
    assert all(not (origin - 192 < stop and origin + 24 > start) for origin in config["train_origins"])


def test_cli_writes_prospective_plan_without_starting_fit(tmp_path, capsys) -> None:
    source = _write_builder_spec(tmp_path)
    selections = {
        "darcy": {"train_sample_ids": [1, 2], "validation_sample_ids": [3]},
        "bloodmnist": {"train_sample_ids": [10, 11], "validation_sample_ids": [12]},
        "etth1": {"train_origins": [192], "validation_origins": [8640]},
    }
    versions = {family: f"{family}-v1" for family in selections}
    updates = {"darcy": 200, "bloodmnist": 400, "etth1": 600}
    paths = {}
    for name, value in (("selection", selections), ("versions", versions), ("updates", updates)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(value))
    output = tmp_path / "prospective-plan.json"
    assert cli(["--plan-mode", "snapshot", "--campaign", str(tmp_path), "--source-spec-dir", str(source),
                "--data-selection-json", str(paths["selection"]), "--dataset-versions-json", str(paths["versions"]),
                "--update-counts-json", str(paths["updates"]), "--plan-output", str(output)]) == 0
    plan = json.loads(output.read_text())
    assert plan["fit_count"] == 72
    assert "no model fit" in plan["note"]
    assert '"fit_count": 72' in capsys.readouterr().out


def test_explicit_dev_config_uses_registered_ids_without_rebuilding_recipe(tmp_path, capsys) -> None:
    campaign = tmp_path / "campaign"
    amendments = campaign / "protocol" / "amendments"
    researcher = campaign / "data" / "researcher"
    amendments.mkdir(parents=True)
    researcher.mkdir(parents=True)
    selection = {"samples": {"D": {"train": [101, 102], "validation": [201]}}}
    (amendments / "dev_selection_v2.0.1.json").write_text(json.dumps(selection))
    np.savez(researcher / "darcy64_train.npz", sample_id=np.asarray([101, 102]),
             x=np.random.default_rng(1).random((2, 64, 64), dtype=np.float32),
             y=np.random.default_rng(2).random((2, 64, 64), dtype=np.float32))
    np.savez(researcher / "darcy64_validation.npz", sample_id=np.asarray([201]),
             x=np.random.default_rng(3).random((1, 64, 64), dtype=np.float32),
             y=np.random.default_rng(4).random((1, 64, 64), dtype=np.float32))
    config = {
        "work_id": "supervised-pilot-101", "phase": "pilot", "formal_score_eligible": False,
        "task_id": "D_DEV", "configuration_id": "D_DEV", "dataset_version_id": "researcher-d-v2",
        "recipe_version_id": "pilot-recipe-v2", "family": "darcy", "seed": 913701,
        "optimizer_updates": 2, "batch_size": 2, "effective_batch_size": 2,
        "optimizer": {"name": "AdamW", "lr": 0.001, "betas": [0.9, 0.999], "eps": 1e-8, "weight_decay": 1e-4},
        "scheduler": {"name": "cosine", "final_lr_fraction": 0.1}, "precision": "float32", "tf32": False,
        "log_every_updates": 25, "fourier_modes_per_axis": 4, "hidden_width": 8, "spectral_layers": 3,
        "gradient_loss_weight": 0.0, "training_sample_count": 2,
        "train_sample_ids": [101, 102], "validation_sample_ids": [201],
        "external_ledger_id": "ledger-v2", "source_revision_reference": "study-runtime-test",
    }
    config_path = tmp_path / "explicit-pilot.json"
    config_path.write_text(json.dumps(config))
    assert cli(["--phase", "pilot", "--campaign", str(campaign), "--config", str(config_path),
                "--output", str(tmp_path / "pilot-out"), "--shape-check", "--device", "cpu"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["phase"] == "pilot" and result["optimizer_updates"] == 0
    assert result["train_shape"] == [2, 3, 64, 64]
    assert json.loads(config_path.read_text()) == config
