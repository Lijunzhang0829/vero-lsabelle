#!/usr/bin/env python3
"""Offline common VERO/Codex experiment metrics; no model or prover calls."""

import argparse
import json

from vero.isabelle.metrics import export_metrics, write_export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metrics",
        required=True,
        help="Directory containing immutable manifest.json and events/",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--as-of", type=float)
    parser.add_argument(
        "--token-thresholds",
        type=int,
        nargs="+",
        default=[100_000_000, 300_000_000, 500_000_000, 1_000_000_000],
    )
    parser.add_argument(
        "--milestones", type=int, nargs="+", default=[10, 30, 50, 75, 100]
    )
    args = parser.parse_args()
    if any(value < 0 for value in args.token_thresholds) or any(
        value <= 0 for value in args.milestones
    ):
        parser.error("token thresholds must be nonnegative and milestones positive")
    bundle = export_metrics(
        args.metrics,
        token_thresholds=args.token_thresholds,
        milestones=args.milestones,
        as_of=args.as_of,
    )
    write_export(bundle, args.output)
    print(json.dumps(bundle["summary"], indent=2))


if __name__ == "__main__":
    main()
