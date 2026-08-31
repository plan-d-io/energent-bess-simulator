"""Expert CLI for the revenue battery-size sweep."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from btm_sim.cli_battery import add_battery_arguments
from btm_sim.config.defaults import standard_defaults_path
from btm_sim.run.exceptions import EXIT_INVALID_REQUEST, EXIT_SUCCESS, RunError
from btm_sim.run.request import new_job_id
from btm_sim.run.status import JobSession
from btm_sim.sweep.candidates import parse_durations, parse_explicit_pairs, parse_mode
from btm_sim.sweep.request import build_sweep_request, load_sweep_request
from btm_sim.sweep.workflow import run_sweep_end_to_end

_STARTING_VALUE_NOTE = "Starting value comes from the selected central defaults file"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btm-sweep",
        description=(
            "Compare a finite set of battery sizes for one site and period using "
            "only fixed-tariff Revenue maximisation. This is a screening estimate, "
            "not an economic optimum or complete business case. Settings follow "
            "CLI > run TOML > configs/defaults.toml. A frozen --request JSON is "
            "the machine-readable entry for another process."
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="Three Fluvius CSV exports (offtake, injection, PV; order does not matter)",
    )
    parser.add_argument(
        "--request",
        type=Path,
        default=None,
        help="Frozen sweep_request.json produced by build_sweep_request / write_sweep_request",
    )
    parser.add_argument("--period", default=None, help="Discovered period id to materialize, for example 2024")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Exact output directory for the sweep audit folder",
    )
    parser.add_argument("--site-label", default=None, help="Optional site or project label recorded in the frozen request")
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
            "measured PV as acknowledged site-boundary exceptions"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional run configuration TOML (overrides only; Fluvius files stay on the command line)",
    )
    parser.add_argument(
        "--defaults",
        type=Path,
        default=None,
        help=f"Central defaults TOML. Default: {standard_defaults_path()}",
    )
    parser.add_argument(
        "--detailed-solver-output",
        action="store_true",
        help="Print detailed HiGHS solver console output (off by default)",
    )
    parser.add_argument(
        "--mode",
        choices=("automatic", "manual_range", "explicit"),
        default=None,
        help="Candidate generation mode. Default: automatic",
    )
    parser.add_argument(
        "--durations",
        default=None,
        help="Comma-separated durations in hours, for example 2,4. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--duration",
        action="append",
        dest="duration_values",
        default=None,
        help="Repeatable duration in hours. May be combined with --durations",
    )
    parser.add_argument("--min-power", type=float, default=None, help="manual_range minimum power in kW")
    parser.add_argument("--max-power", type=float, default=None, help="manual_range maximum power in kW")
    parser.add_argument("--power-increment", type=float, default=None, help="manual_range power increment in kW")
    parser.add_argument(
        "--candidate",
        action="append",
        dest="candidates",
        default=None,
        help="explicit candidate as POWER,ENERGY. Repeatable",
    )
    parser.add_argument(
        "--estimated-battery-cost-eur-per-kwh",
        "--estimated-battery-cost",
        type=float,
        default=None,
        dest="estimated_battery_cost_eur_per_kwh",
        help="Estimated battery cost in EUR/kWh usable. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--evaluation-period-years",
        type=float,
        default=None,
        help="Evaluation period in years for estimated value. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument(
        "--revenue-capture-threshold",
        type=float,
        default=None,
        dest="revenue_capture_threshold_pct",
        help="Revenue-capture threshold as a percent of the duration's max annual uplift. " + _STARTING_VALUE_NOTE,
    )
    add_battery_arguments(parser, required=False, starting_value_note=_STARTING_VALUE_NOTE)
    parser.add_argument("--customer-rate", type=float, default=None, help="Customer PV-sale rate, EUR/MWh. " + _STARTING_VALUE_NOTE)
    parser.add_argument("--export-peak-rate", type=float, default=None, help="Peak-period export rate, EUR/MWh. " + _STARTING_VALUE_NOTE)
    parser.add_argument("--export-offpeak-rate", type=float, default=None, help="Off-peak export rate, EUR/MWh. " + _STARTING_VALUE_NOTE)
    parser.add_argument("--peak-start", default=None, help="Local peak start HH:MM, inclusive. " + _STARTING_VALUE_NOTE)
    parser.add_argument("--peak-end", default=None, help="Local peak end HH:MM, exclusive. " + _STARTING_VALUE_NOTE)
    parser.add_argument(
        "--weekends-offpeak",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Treat all Saturday and Sunday intervals as off-peak. " + _STARTING_VALUE_NOTE,
    )
    parser.add_argument("--timezone", default=None, help="Timezone for tariff classification. " + _STARTING_VALUE_NOTE)
    return parser


def _cli_overrides(args: argparse.Namespace) -> dict:
    durations = _combined_durations(args)
    values = {
        "e_usable": args.e_usable,
        "p_charge": args.p_charge,
        "p_discharge": args.p_discharge,
        "power": args.power,
        "eta_charge": args.eta_charge,
        "eta_discharge": args.eta_discharge,
        "soc_initial": args.soc_initial,
        "max_equivalent_full_cycles_per_year": args.max_equivalent_full_cycles_per_year,
        "customer_rate": args.customer_rate,
        "export_peak_rate": args.export_peak_rate,
        "export_offpeak_rate": args.export_offpeak_rate,
        "peak_start": args.peak_start,
        "peak_end": args.peak_end,
        "weekends_offpeak": args.weekends_offpeak,
        "timezone": args.timezone,
        "estimated_battery_cost_eur_per_kwh": args.estimated_battery_cost_eur_per_kwh,
        "evaluation_period_years": args.evaluation_period_years,
        "default_durations_hours": durations,
        "revenue_capture_threshold_pct": args.revenue_capture_threshold_pct,
        "output_dir": args.output_dir,
    }
    return {key: value for key, value in values.items() if value is not None}


def _combined_durations(args: argparse.Namespace) -> list[float] | None:
    parts: list[str] = []
    if args.durations:
        parts.extend(part.strip() for part in str(args.durations).split(",") if part.strip())
    if args.duration_values:
        parts.extend(str(item) for item in args.duration_values)
    if not parts:
        return None
    return list(parse_durations(parts, name="durations"))


def _has_setting_overrides(args: argparse.Namespace) -> bool:
    if args.config is not None or args.defaults is not None:
        return True
    if args.period or args.site_label or args.allow_unvalidated or args.acknowledge_site_boundary:
        return True
    if args.detailed_solver_output:
        return True
    if args.mode or args.durations or args.duration_values or args.candidates:
        return True
    if args.min_power is not None or args.max_power is not None or args.power_increment is not None:
        return True
    return bool(_cli_overrides(args))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    session: JobSession | None = None
    try:
        if args.request is not None:
            if args.inputs:
                print("Do not combine --request with Fluvius input files", file=sys.stderr)
                return EXIT_INVALID_REQUEST
            if args.output_dir is not None or _has_setting_overrides(args):
                print(
                    "Do not combine --request with setting overrides; freeze them in the request file",
                    file=sys.stderr,
                )
                return EXIT_INVALID_REQUEST
            request = load_sweep_request(args.request)
            result = run_sweep_end_to_end(request, console=True)
        else:
            if len(args.inputs) != 3:
                print("Provide exactly three Fluvius CSV exports, or --request", file=sys.stderr)
                return EXIT_INVALID_REQUEST
            if args.period is None:
                print("Provide --period when running from Fluvius files", file=sys.stderr)
                return EXIT_INVALID_REQUEST
            if args.output_dir is None:
                print("Provide --output-dir when running from Fluvius files", file=sys.stderr)
                return EXIT_INVALID_REQUEST
            session = JobSession.create(Path(args.output_dir).resolve(), new_job_id())
            durations = _combined_durations(args)
            request = build_sweep_request(
                fluvius_paths=args.inputs,
                period_id=args.period,
                output_dir=session.output_dir,
                allow_unvalidated=args.allow_unvalidated,
                acknowledge_site_boundary=args.acknowledge_site_boundary,
                site_label=args.site_label,
                defaults_path=args.defaults,
                run_toml_path=args.config,
                detailed_solver_output=args.detailed_solver_output,
                mode=parse_mode(args.mode),
                durations_hours=durations,
                min_power_kw=args.min_power,
                max_power_kw=args.max_power,
                power_increment_kw=args.power_increment,
                explicit_pairs=parse_explicit_pairs(args.candidates),
                cli=_cli_overrides(args),
                job_id=session.job_id,
            )
            result = run_sweep_end_to_end(request, console=True, session=session)
    except BrokenPipeError:
        return EXIT_SUCCESS
    except RunError as exc:
        if session is not None and session.snapshot().get("state") != "failed":
            session.write_exception(exc)
            session.fail(exc.category, str(exc))
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        if session is not None and session.snapshot().get("state") != "failed":
            session.write_exception(exc)
            session.fail("execution", str(exc))
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()

    recommendation = None
    if result.sweep is not None:
        recommendation = result.sweep.recommendation["recommendation"]
    print(
        json.dumps(
            {
                "ok": result.ok,
                "output_dir": str(result.directory),
                "job_id": result.request.job_id,
                "artifact_schema_version": result.status.get("artifact_schema_version"),
                "status": result.status.get("state"),
                "n_candidates": len(result.request.candidates),
                "suggested_battery_size": None
                if recommendation is None
                else {
                    "kind": recommendation.get("recommendation_kind"),
                    "label": recommendation.get("label"),
                    "candidate_id": recommendation.get("candidate_id"),
                    "power_kw": recommendation.get("power_kw"),
                    "usable_energy_kwh": recommendation.get("usable_energy_kwh"),
                    "estimated_value_eur": recommendation.get("estimated_value_eur"),
                },
            },
            indent=2,
        ),
        flush=True,
    )
    return EXIT_SUCCESS if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
