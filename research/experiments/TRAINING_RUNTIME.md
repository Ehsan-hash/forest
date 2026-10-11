# FOREST training runtime

`research.experiments.training_runtime` is the reusable per-fit engine for the
three registered model families. It reads only the named files under
`campaign/data/researcher`; paths to raw archives and evaluator data are not
accepted. Every fit receives explicit work, task, configuration, dataset
version, seed, recipe, and ordered sample IDs. The run folder stores that full
configuration, periodic JSONL training observations, an atomic restartable
checkpoint, validation predictions/metrics, and an execution receipt.

## Registered development interfaces

Run zero-update interface checks against the actual researcher split and exact
development IDs:

```bash
cd /mnt/c/Users/space/Desktop/FOREST_Paper_Compact_v2/FOREST-source
PY=/home/space/FOREST_Paper_Compact_v2/FOREST-source/.venv/bin/python
CAMPAIGN=/home/space/FOREST_Paper_Compact_v2/campaigns/forest-paper-compact-v2-20261010
$PY -m research.experiments.training_runtime --phase pilot --task D_DEV --campaign "$CAMPAIGN" --output /tmp/forest-D_DEV-shape --device cpu --shape-check
$PY -m research.experiments.training_runtime --phase pilot --task M_DEV --campaign "$CAMPAIGN" --output /tmp/forest-M_DEV-shape --device cpu --shape-check
$PY -m research.experiments.training_runtime --phase pilot --task T_DEV --campaign "$CAMPAIGN" --output /tmp/forest-T_DEV-shape --device cpu --shape-check
```

Each development fit must use the registered `D_DEV`, `M_DEV`, or `T_DEV`
recipe and must remain `formal_score_eligible=false`. A real fit uses the same
command with `--device cuda` and without `--shape-check`. Real CUDA fits must
be launched through the campaign's approved external GPU supervisor, which
owns lease limits and resource accounting. The runtime itself imposes no fit
timeout. Supply `--work-id` to match a supervisor-issued job ID; otherwise the
output directory name is the stable work ID. Development timings are not used
to extrapolate formal fit times.

## Calibration and formal fit interface

`build_snapshot_fit_configs` assembles every registered task's three candidate
recipes and the requested seeds into complete fit configs. Supply exact
ordered researcher IDs/origins, dataset version IDs, and one permitted update
count per family as JSON files. The output is prospective configuration only;
no fit is started:

```bash
$PY -m research.experiments.training_runtime --plan-mode snapshot --campaign "$CAMPAIGN" --use-researcher-split-ids --dataset-versions-json /path/to/dataset-versions.json --update-counts-json /path/to/update-counts.json --plan-output "$CAMPAIGN/development/v2.0.1/training_runtime/snapshot-plan.json"
$PY -m research.experiments.training_runtime --phase formal --plan-input "$CAMPAIGN/development/v2.0.1/training_runtime/snapshot-plan.json" --plan-index 0 --campaign "$CAMPAIGN" --output /path/to/independent-run-directory --device cuda
```

The analogous calibration plan includes per-family anchor, slowest combined
candidate, C3, and C4 fits. Give it the allowed update level for each family.
By default, C3 uses PCG64 with mutation seed 20261012 over the caller's ordered
researcher IDs. Darcy removes complete fields; BloodMNIST removes within each
class and retains at least one image in every class; ETTh1 removes the seeded
432-row contiguous interval and filters origins against the maximum context
and horizon. The generator state, selected IDs or interval, and resulting
cohort are saved in the fit config. Supply `--c3-overrides-json` only for an
explicitly versioned alternative:

```bash
$PY -m research.experiments.training_runtime --plan-mode calibration --campaign "$CAMPAIGN" --use-researcher-split-ids --dataset-versions-json /path/to/dataset-versions.json --update-counts-json /path/to/update-counts.json --plan-output "$CAMPAIGN/development/v2.0.1/training_runtime/calibration-plan.json"
$PY -m research.experiments.training_runtime --phase calibration --plan-input "$CAMPAIGN/development/v2.0.1/training_runtime/calibration-plan.json" --plan-index 0 --campaign "$CAMPAIGN" --output /path/to/independent-run-directory --device cuda
```

`--use-researcher-split-ids` materializes explicit ordered IDs from the
allowlisted Darcy/BloodMNIST researcher NPZs and the declared ETTh1 train and
validation origin intervals. To use a separately registered subset, pass its
ordered IDs with `--data-selection-json` instead. Dataset version IDs and
update counts remain explicit caller inputs. The builders read current
`models.json` and `tasks.json` as editable inputs and
do not use hashes or write frozen protocol records. `build_snapshot_fit_configs`
produces 72 configs for the current 12-task, three-candidate, two-seed plan;
each includes the full original task candidate and its anchor designation.
`enumerate_long_configurations` lists the current nine candidates for D01/D03,
M01/M03, or T01/T03. `build_calibration_fit_configs` produces 12 prospective
configs (four roles per family) with C3 selection materialized from the
registered mutation seed. `build_episode_fit_config` builds one full original
task candidate for a paired C3/C4 episode, including its cohort or objective
revision and version labels. These helpers create configs only; they never
start model fits.

All actual CUDA fit commands must be run by the approved external supervisor.
The command shown above is the child fit interface; do not launch it outside
the supervisor's GPU lease and accounting path.

Each config specifies `work_id`, `phase`, `task_id`, `configuration_id`,
`dataset_version_id`, `family`, `seed`, `optimizer_updates`, and `batch_size`.
It also carries the complete model and recipe values, explicit ordered
`train_sample_ids` and `validation_sample_ids` (or ETTh1 origins), optimizer,
schedule, transformations, and any registered C3/C4 change. Set
`effective_batch_size` equal to `batch_size`; gradient accumulation is not
implemented. The calibration role is explicit (`anchor`, `slowest_candidate`,
or a registered revision label). `validate_calibration_bundle` checks role
coverage and caller-supplied allowed update counts without picking a winner
or choosing a new update count. Optimizer, scheduler, precision and TF32
settings remain explicit config inputs. The runtime checks AdamW and cosine
schedule values for finite, supported values without requiring the current
default learning rate or regularization constants. Changing a recipe requires
a new `recipe_version_id`.

The explicit revision fields supported by the data/objective interfaces are:

- C2 validation changes: `validation_noise_std` for Darcy/BloodMNIST,
  `validation_noise_std_in_training_sd` for ETTh1, plus `validation_noise_seed`.
- C3: provide a revised explicit training-ID list for Darcy/BloodMNIST. For
  ETTh1 provide `excluded_training_row_interval: [start, stop]`; the runtime
  omits every training origin whose fixed 192-row maximum-context window or
  24-step target intersects that interval, and excludes those rows from the
  training-only scaler.
- C4: use `c4_gradient_mse_weight` for Darcy's original-scale two-axis
  forward-gradient penalty, or `c4_weight_l2` for the mean squared trainable
  weight penalty in BloodMNIST and ETTh1. These explicit recipe changes are
  recorded in the full fit config.

C1 metric revisions do not require training or another model pass. Reuse the
saved `validation_predictions.npz` and call `score_predictions` with the
matching prepared validation targets to compute all registered metrics.
Checkpoints include model, optimizer, cosine scheduler, sampler permutation
and cursor, completed update count, loss log, Python/NumPy/Torch CPU/CUDA RNG
states, and the entire effective config. Resume requires an identical full
config and refuses mismatched runs.

The runtime does not read or record data/code content hashes. An external
supervisor receipt is the source of authoritative lease timing; the runtime
receipt preserves the effective recipe, sample IDs, observed training time,
environment, metrics, and prediction filename. Pilot outputs are excluded
from formal summaries by their explicit phase/eligibility fields.
