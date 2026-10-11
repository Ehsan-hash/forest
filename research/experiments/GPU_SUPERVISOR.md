# Durable compute supervision

`python -m research.experiments.gpu_supervisor` uses an existing SQLite resource
ledger. It preserves every legacy row and adds `forest_compute_*` tables for
explicit work IDs, complete accepted execution conditions, policy revisions,
original process observations, and measured intervals. It computes shared GPU
consumption from legacy `gpu_allocated_ms` rows plus all new GPU intervals in the
same physical SQLite ledger. Different work IDs, phases, or logical ledger IDs
cannot reset that consumption.

## Configure actual allowances

```sh
python -m research.experiments.gpu_supervisor configure \
  --ledger /absolute/campaign/study.sqlite \
  --ledger-id campaign:study.sqlite --campaign-id campaign \
  --gpu-budget-seconds 172800 --wall-start 1791720897 --wall-seconds 172800
```

The values come from the owner's actual research contract. An existing campaign
GPU allowance cannot be exceeded. Updating a policy retains both the old policy
revision and every original usage interval. Phases are recorded without quotas.
The default 3.25-second termination reservation covers the independent process
host's TERM grace and polling; a smaller reservation is rejected. This reserve is
execution stop latency, rather than a restriction on scientific decisions.

## Submit accepted work

Create an explicit output workspace and a complete execution configuration JSON.
Its optional `source_files` list names original code/configuration files outside
the output workspace whose actual accepted contents should be retained.
`code_inputs` retains existing ManagedProcesses workspace source selection.

```sh
python -m research.experiments.gpu_supervisor submit \
  --ledger /absolute/campaign/study.sqlite --ledger-id campaign:study.sqlite \
  --work-id accepted-work-uuid --workspace /absolute/output \
  --command-json '["/absolute/python", "-m", "research.experiments.training_runtime", "--device", "cuda", "--output", "/absolute/output"]' \
  --configuration /absolute/execution-config.json \
  --env-json '{"PYTHONPATH":"/absolute/FOREST-source"}' \
  --gpu-device 0 --phase pilot --timeout-seconds 3600
```

The fit timeout is supplied by the execution configuration; no global fit timeout
or lifetime step quota is imposed. The supervisor applies the remaining actual
GPU and wall allowance and reserves original owned work. GPU index/UUID aliases
resolve to the same physical UUID. With no `--gpu-device`, the child receives
`CUDA_VISIBLE_DEVICES=''` and its actual time is recorded as CPU, not GPU usage.

Accepted work retains its original full command, environment, configuration,
physical selection, and source originals. Repeating the same work ID reuses its
original running or completed job. Changed execution conditions require a new
work ID; that creates a legitimate new computation while keeping the old receipt.
An edit to accepted source before its first launch requires reconciliation rather
than executing different code under an old accepted identity.

## Read and resume ownership

```sh
python -m research.experiments.gpu_supervisor wait \
  --ledger /absolute/campaign/study.sqlite --ledger-id campaign:study.sqlite \
  --work-id accepted-work-uuid

python -m research.experiments.gpu_supervisor status \
  --ledger /absolute/campaign/study.sqlite --ledger-id campaign:study.sqlite
```

`wait` recreates a lost metering supervisor and reconnects to the original process
identity and receipt. Its optional `--wait-timeout` only bounds the caller's wait.
Original launch intent is committed before process creation. A crash in that
uncertain effect boundary produces `reconciliation` and never blindly relaunches.
An independently bounded original process host remains responsible for stopping
the job if the metering supervisor crashes. All original control records and
snapshots reside under `.forest-compute` beside the SQLite ledger, outside the
editable output workspace.

Meter intervals and the accounted endpoint commit in one SQLite transaction;
recovery records only unaccounted elapsed time. A confirmed stopped job's usage
does not grow when its historical artifact later needs repair. Missing or changed
retained source/result originals produce an explicit integrity error and cannot
be reported as a valid successful scientific result.

## Validation

`tests/test_gpu_supervisor.py` uses real short CPU child processes to exercise
receipt publication, repeated submission, supervisor SIGKILL/restart, corrupted
receipts, actual timeout cancellation, original source revisions, CLI environment,
and stopped-job accounting. Its GPU alias/allocation tests use clearly constructed
policy records and do not claim to have run a physical GPU fit.
