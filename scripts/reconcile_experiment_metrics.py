#!/usr/bin/env python3
"""Import persisted response usage after a crash, without calling any model."""

import argparse
import json

from vero.isabelle.response_usage import reconcile_usage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", required=True)
    parser.add_argument(
        "--codex-home",
        required=True,
        help="Experiment-specific Codex session directory; credentials are never read",
    )
    args = parser.parse_args()
    print(
        json.dumps(reconcile_usage(args.metrics, codex_home=args.codex_home), indent=2)
    )


if __name__ == "__main__":
    main()
