#!/usr/bin/env python3
"""Offline v2 target/shared-cost and valid-seal curves; never invoke a model."""

import argparse
import json

from vero.isabelle.resource_metrics import (
    export_resource_metrics,
    write_resource_export,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--token-thresholds", type=int, nargs="+", default=[])
    parser.add_argument(
        "--milestones", type=int, nargs="+", default=[10, 30, 50, 75, 100]
    )
    parser.add_argument("--as-of", type=float)
    parser.add_argument(
        "--cutoff", type=float, help="Absolute Unix timestamp; e.g. stage1 deadline_at"
    )
    args = parser.parse_args()
    if any(n < 0 for n in args.token_thresholds) or any(
        n <= 0 for n in args.milestones
    ):
        parser.error("thresholds must be nonnegative; milestones must be positive")
    result = export_resource_metrics(
        args.metrics,
        token_thresholds=args.token_thresholds,
        milestones=args.milestones,
        as_of=args.as_of,
        cutoff=args.cutoff,
    )
    write_resource_export(result, args.output)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
