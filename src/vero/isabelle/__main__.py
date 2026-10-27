import argparse
import json

from .benchmark import extract, prepare, read, render, save
from .checker import Checker, evaluate
from .environment import verify
from .runner import run


def main():
    p = argparse.ArgumentParser(description="Vero Isabelle proof-mode backend")
    s = p.add_subparsers(dest="command", required=True)
    x = s.add_parser("verify-environment")
    x.add_argument("--environment", required=True)
    x = s.add_parser("prepare")
    x.add_argument("--contracts", required=True)
    x.add_argument("--output", required=True)
    x.add_argument("--base-import", default="Semantic_Base.Semantic_Root")
    x = s.add_parser("upgrade-slots")
    x.add_argument("--benchmark", required=True)
    x.add_argument("--output", required=True)
    x = s.add_parser("render")
    x.add_argument("--benchmark", required=True)
    x.add_argument("--output", required=True)
    x = s.add_parser("extract")
    x.add_argument("--benchmark", required=True)
    x.add_argument("--source", required=True)
    x.add_argument("--output", required=True)
    x = s.add_parser("evaluate")
    x.add_argument("--benchmark", required=True)
    x.add_argument("--artifact", required=True)
    x.add_argument("--checker", required=True)
    x.add_argument("--output", required=True)
    x = s.add_parser("evaluate-samples")
    x.add_argument("--benchmark", required=True)
    x.add_argument("--samples", required=True)
    x.add_argument("--checker", required=True)
    x.add_argument("--output", required=True)
    x = s.add_parser("plan-run")
    x.add_argument("--benchmark", required=True)
    x.add_argument("--config", required=True)
    x.add_argument("--output", required=True)
    x.add_argument("--resume", action="store_true")
    x = s.add_parser("run")
    x.add_argument("--benchmark", required=True)
    x.add_argument("--config", required=True)
    x.add_argument("--output", required=True)
    x.add_argument("--resume", action="store_true")
    a = p.parse_args()
    if a.command == "verify-environment":
        result = verify(a.environment)
    elif a.command == "prepare":
        result = prepare(a.contracts, a.output, base_import=a.base_import)
    elif a.command == "upgrade-slots":
        from .slots import upgrade

        result = upgrade(read(a.benchmark))
        save(a.output, result)
    elif a.command == "render":
        result = render(read(a.benchmark), a.output)
    elif a.command == "extract":
        result = extract(read(a.benchmark), a.source)
        save(a.output, result)
    elif a.command == "evaluate":
        result = evaluate(
            read(a.benchmark), read(a.artifact), a.output, Checker(read(a.checker))
        )
    elif a.command == "evaluate-samples":
        from .sampling import grade_samples

        result = grade_samples(
            read(a.benchmark), a.samples, a.output, Checker(read(a.checker))
        )
    elif a.command == "plan-run":
        from .planned_run import execute

        result = execute(a.benchmark, a.config, a.output, resume=a.resume)
    else:
        result = run(a.benchmark, a.config, a.output, resume=a.resume)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
