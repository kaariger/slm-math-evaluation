"""Command-line entry point for the native evaluation path."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import analysis, data, runner, runtime


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise data.DataError(message, 3)


def parser() -> Parser:
    root = Parser(prog="slm-eval")
    commands = root.add_subparsers(dest="command", required=True, parser_class=Parser)
    data_command = commands.add_parser("data")
    data_commands = data_command.add_subparsers(dest="data_command", required=True, parser_class=Parser)
    data_commands.add_parser("fetch")
    build = data_commands.add_parser("build-manifest")
    build.add_argument("--out", required=True, type=Path)
    build.add_argument("--flagged-out", type=Path)
    show = data_commands.add_parser("show-pair")
    show.add_argument("--flagged", required=True, type=Path)
    show.add_argument("--pair", required=True, type=int)
    verdicts = data_commands.add_parser("apply-verdicts")
    verdicts.add_argument("--manifest", required=True, type=Path)
    verdicts.add_argument("--verdicts", required=True, type=Path)
    verdicts.add_argument("--flagged", type=Path)
    verify_runtime = commands.add_parser("verify-runtime")
    verify_runtime.add_argument("--protocol", required=True, type=Path)
    verify_artifact = commands.add_parser("verify-artifact")
    verify_artifact.add_argument("--protocol", required=True, type=Path)
    rescore = commands.add_parser("rescore")
    rescore.add_argument("--run", required=True)
    report = commands.add_parser("report")
    report.add_argument("--run", required=True)
    report.add_argument("--compare-published", type=float)
    report.add_argument("--sensitivity", type=Path)
    report.add_argument("--out-dir", type=Path)
    sensitivity = commands.add_parser("sensitivity")
    sensitivity.add_argument("--base", required=True)
    sensitivity.add_argument("--variant", required=True, action="append")
    sensitivity.add_argument("--out", required=True, type=Path)
    run = commands.add_parser("run")
    run.add_argument("--protocol", type=Path)
    run.add_argument("--manifest", type=Path)
    run.add_argument("--tier", choices=("smoke", "validation", "test"))
    run.add_argument("--k", type=int, default=1)
    run.add_argument("--seed-base", type=int, default=0)
    run.add_argument("--resume")
    return root


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.command == "verify-runtime":
            paths = [runtime.verify_runtime(args.protocol)]
        elif args.command == "verify-artifact":
            paths = [runtime.verify_artifact(args.protocol)]
        elif args.command == "rescore":
            paths = [analysis.rescore(args.run)]
        elif args.command == "report":
            paths = analysis.report(args.run, args.compare_published, args.sensitivity, args.out_dir)
        elif args.command == "sensitivity":
            paths = [analysis.sensitivity(args.base, args.variant, args.out)]
        elif args.command == "run":
            if args.resume:
                if args.protocol or args.manifest or args.tier or args.k != 1 or args.seed_base != 0:
                    raise data.DataError("--resume cannot be combined with run-start options", 3)
                print(runner.resume(args.resume))
            else:
                if not (args.protocol and args.manifest and args.tier):
                    raise data.DataError("--protocol, --manifest, and --tier are required", 3)
                print(runner.start(args.protocol, args.manifest, args.tier, args.k, args.seed_base))
            return 0
        elif args.data_command == "fetch":
            paths = data.fetch()
        elif args.data_command == "build-manifest":
            paths = data.build_manifest(args.out, args.flagged_out)
        elif args.data_command == "show-pair":
            first, second = data.show_pair(args.flagged, args.pair)
            print(first)
            print("\n---\n")
            print(second)
            return 0
        else:
            paths = [data.apply_verdicts(args.manifest, args.verdicts, args.flagged)]
        for path in paths:
            print(path)
        return 0
    except data.DataError as error:
        print(error, file=sys.stderr)
        return error.code
    except runtime.RuntimeErrorWithCode as error:
        print(error, file=sys.stderr)
        return error.code
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
