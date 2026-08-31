# End-to-end run

The supported operation from three Fluvius CSV exports to a complete
schema-version-2 comparison folder is `btm-run` (`python -m btm_sim.run`).
It re-reads the source files at execution time, reports progress while it
runs, and leaves request, status, event, and log files when it succeeds or
fails.

This command is the primary orchestration entry for the six-case comparison.
The revenue battery-size sweep is a separate command (`btm-sweep` /
`python -m btm_sim.sweep`); see [`docs/SWEEP.md`](SWEEP.md). Existing
commands (`btm-normalize`, `btm-compare`, and the standalone
battery/optimizer CLIs) keep working and do not write job-status files.

## Python API

Callers should use these public names. Do not construct the request JSON by
hand.

```python
from btm_sim.run import (
    build_run_request,
    write_run_request,
    load_run_request,
    run_end_to_end,
)
```

`build_run_request` resolves settings with the established precedence,
hashes the three Fluvius files, and returns a frozen `EndToEndRunRequest`.
`write_run_request` / `serialize_run_request` persist that object.
`load_run_request` reloads it and validates the frozen values again without
re-merging `configs/defaults.toml`.
`run_end_to_end(request)` is synchronous, writes the audit folder, and
returns `EndToEndRun`.

Optional `progress=` accepts any object with `emit(event)`. The default
additional reporter is a no-op; the run still writes job files.

## Ordinary CLI

```powershell
python -m btm_sim.run offtake.csv injection.csv pv.csv `
  --period 2024 `
  --output-dir outputs\my_run
```

Roles are detected from `Register`, not filenames. `--period` and
`--output-dir` are required on this path. `--allow-unvalidated` and
`--acknowledge-site-boundary` match `btm-normalize`. Battery, tariff, and
reporting flags match `btm-compare`. `--config` is an optional run TOML
(overrides only). `--defaults` selects another central defaults file.
`--dynamic-injection-prices` overrides the standard day-ahead file.
`--site-label` is an optional display name. `--detailed-solver-output`
enables detailed output from the active solver (off by default).

## Frozen-request CLI

```powershell
python -m btm_sim.run --request path\run_request.json
```

Do not combine `--request` with Fluvius files or setting overrides. A
separate UI process should call `build_run_request` and `write_run_request`,
then launch this command.

## Configuration precedence

Ordinary construction uses:

```text
explicit CLI value > run configuration TOML > configs/defaults.toml
```

The frozen request stores already resolved effective values and their
sources (`defaults_toml`, `run_toml`, or `cli`). Editing
`configs/defaults.toml` after freeze cannot change that run. The worker
re-validates the frozen battery, tariff, reporting, path, and hash values.

## Stages

There are ten stable stage keys, in this order:

1. `read_fluvius`
2. `normalize_period`
3. `check_prices`
4. `run_reference`
5. `optimize_self_consumption`
6. `optimize_peak_reduction`
7. `optimize_revenue`
8. `optimize_dynamic_injection`
9. `write_artifacts`
10. `verify_complete`

Progress events are JSON objects with `event_time_utc`, `level`
(`info` / `warning` / `error`), `stage_key`, `stage_number`, `stage_total`,
`state` (`started` / `completed` / `failed`), `message`, and optional
`details`. A stage counter such as `6 of 10` is the progress model. Raw solver
text is not parsed and is not turned into a percentage.

## Job files

The exact output directory is created before Fluvius validation. Every run
writes:

```text
run_request.json    Frozen request: inputs, hashes, effective settings, sources
run_status.json     Atomic current state (temporary file then replace)
run_events.jsonl    One structured progress event per line, flushed promptly
run.log             Readable chronological log, flushed promptly
```

`run_status.json` uses `status_schema_version` 1 and includes job ID, state
(`queued`, `running`, `completed`, or `failed`), current stage, message,
UTC timestamps, worker PID, elapsed seconds, output directory, final
`artifact_schema_version` when completed, and error category/message when
failed.

A failed job still leaves those four files when an output directory could be
established. The run is not marked completed until the schema-version-2
folder passes the existing consistency checks.

The final folder also contains the usual comparison artifacts, including
one `normalized_input.parquet` and one `validation_report.json`. The three
raw Fluvius CSVs are not copied; their original names and SHA-256 hashes
are recorded in the request and validation report.

## Exit codes

- `0`: completed successfully
- `2`: invalid request, configuration, inputs, period, or price coverage
- `1`: execution, solver, or artifact-writing failure

Plain progress messages go to stdout with flushing enabled. The final
successful CLI line is a short JSON object (`ok`, `output_dir`, `job_id`).

## Detailed solver output

`--detailed-solver-output` (or `detailed_solver_output=True` on the request)
enables detailed logging on the active optimizer backend through the six-case
comparison. HiGHS implements this through the native solver log stream. It is
off by default. Extra solver text is written to the console and appended to
`run.log`. Structured status remains the source of truth for progress.
