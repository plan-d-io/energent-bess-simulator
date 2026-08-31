"""Command-line entry point for Fluvius normalization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from btm_sim.fluvius.pipeline import ingest_fluvius, normalize_fluvius, write_run_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btm-normalize",
        description="Normalize Fluvius offtake, injection, and PV exports to canonical quarter-hours.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Fluvius CSV exports. Roles are detected from Register, not filenames.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for normalized_input.parquet and validation_report.json",
    )
    parser.add_argument(
        "--period",
        default="common",
        help="Discovered period id to materialize (default: common overlap)",
    )
    parser.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Use non-null Ongevalideerd readings and record that acknowledgement",
    )
    parser.add_argument(
        "--acknowledge-site-boundary",
        action="store_true",
        help=(
            "Treat material negative reconstructed load or export materially above "
            "measured PV as acknowledged site-boundary exceptions. Ordinary "
            "simultaneous import and export in one quarter-hour does not require "
            "this flag: those registers are directional energy totals, not "
            "instantaneous power."
        ),
    )
    parser.add_argument(
        "--list-periods",
        action="store_true",
        help="Discover periods and write the validation report without parquet",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    missing = [str(path) for path in args.inputs if not path.exists()]
    if missing:
        print(f"Input file not found: {', '.join(missing)}", file=sys.stderr)
        return 2

    if args.list_periods:
        ingest = ingest_fluvius(
            args.inputs,
            allow_unvalidated=args.allow_unvalidated,
            acknowledge_site_boundary=args.acknowledge_site_boundary,
        )
        from btm_sim.fluvius.pipeline import NormalizationResult, ingest_report

        result = NormalizationResult(
            issues=ingest.issues,
            frame=None,
            periods=ingest.periods,
            selected_period=None,
            report=ingest_report(ingest),
            ingest=ingest,
        )
        result.report["ok"] = ingest.ok
        write_run_outputs(result, args.output_dir)
        print(json.dumps({"ok": ingest.ok, "periods": result.report["periods"]}, indent=2))
        return 0 if ingest.ok else 1

    result = normalize_fluvius(
        args.inputs,
        period=args.period,
        allow_unvalidated=args.allow_unvalidated,
        acknowledge_site_boundary=args.acknowledge_site_boundary,
        output_dir=args.output_dir,
    )
    summary = {
        "ok": result.ok,
        "period": None if result.selected_period is None else result.selected_period.id,
        "n_rows": result.report.get("n_rows"),
        "n_fatal": len(result.report["fatal"]),
        "n_warnings": len(result.report["warnings"]),
        "outputs": result.output_paths,
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
