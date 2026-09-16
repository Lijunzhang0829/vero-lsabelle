#!/usr/bin/env python3
"""Deploy a sanitized, unprivileged agent and separate privileged evaluator.

Only run against an environment whose public data, runtime and heaps have been
verified. This script never mounts the original seL4 checkout in the worker.
"""

import argparse
import json
import pathlib
import subprocess


def call(*args):
    return subprocess.run(args, check=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--environment", required=True)
    p.add_argument(
        "--benchmark",
        help="Benchmark directory; defaults to benchmarks/RefineIsabelleV2 (use RefineIsabelle explicitly for legacy replay)",
    )
    p.add_argument("--isabelle", required=True)
    p.add_argument("--codex", required=True)
    p.add_argument("--auth", required=True)
    p.add_argument("--run-root", required=True)
    p.add_argument("--name", default="vero-isabelle-refine")
    p.add_argument("--image", default="sel4-public:tuned")
    p.add_argument("--agent-seconds", type=int, default=1800)
    p.add_argument("--proxy", help="HTTP proxy reachable on the worker host loopback")
    a = p.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    env = pathlib.Path(a.environment).resolve()
    benchmark = (
        pathlib.Path(a.benchmark).resolve()
        if a.benchmark
        else repo / "benchmarks/RefineIsabelleV2"
    )
    run = pathlib.Path(a.run_root).resolve()
    for key in ["sandbox", "results", "control", "agent-home"]:
        (run / key).mkdir(parents=True, exist_ok=True)
    for companion in ["codex", "codex-code-mode-host", "rg"]:
        if not (pathlib.Path(a.codex).resolve().parent / companion).is_file():
            raise FileNotFoundError("missing Codex runtime companion: " + companion)
    uid = 1001
    checker = {
        "session": "Semantic_Base",
        "isabelle": "/workspace/verification/isabelle/bin/isabelle",
        "timeout_seconds": 120,
        "threads": 4,
        "session_dirs": [
            "/sel4-project/verification/l4v/" + path
            for path in [
                "lib/Word_Lib",
                "lib/ML_Utils",
                "lib/Basics",
                "lib/Eisbach_Tools",
                "lib/Monads",
                "lib/sep_algebra",
                "lib",
                "tools/c-parser",
                "tools/autocorres",
                "tools/asmrefine",
                "spec",
                "proof",
            ]
        ]
        + ["/semantic-base"],
        "env": {"HOME": "/home/agent", "USER_HOME": "/home/agent", "L4V_ARCH": "ARM"},
    }
    (benchmark / "checker.json").write_text(json.dumps(checker, indent=2) + "\n")
    config = {
        "sandbox": "/sandbox",
        "sandbox_uid": uid,
        "max_iterations": 1,
        "total_agent_seconds": a.agent_seconds,
        "feedback": True,
        "resume_artifact": False,
        "agent": {
            "codex": "/opt/vero/codex",
            "model": "gpt-6-astra",
            "sandbox_mode": "danger-full-access",
            "external_isolation": "docker-nonroot",
            "reasoning_effort": "medium",
            "timeout_seconds": a.agent_seconds,
            "home": "/home/agent",
            "user": "agent",
            "command_prefix": ["runuser", "-u", "agent", "--"],
        },
        "checker": {
            **checker,
            "command_prefix": ["runuser", "-u", "checker", "--"],
            "env": {
                **checker["env"],
                "HOME": "/home/checker",
                "USER_HOME": "/home/checker",
            },
        },
    }
    (run / "control/config.json").write_text(json.dumps(config, indent=2) + "\n")
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        a.name,
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "SETUID",
        "--cap-add",
        "SETGID",
        "--cap-add",
        "CHOWN",
        "--cap-add",
        "FOWNER",
        "--cap-add",
        "DAC_OVERRIDE",
        "--cap-add",
        "KILL",
        "--tmpfs",
        "/tmp:exec,size=4g",
        "--tmpfs",
        "/sel4-project:exec",
        "--tmpfs",
        "/workspace:exec",
        "-e",
        "L4V_ARCH=ARM",
        "-e",
        "PYTHONPATH=/vero-src",
        "-e",
        "VERO_SRC=/vero-src",
        "-e",
        "PATH=/workspace/verification/isabelle/bin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    ]
    if a.proxy:
        cmd += [
            "--network",
            "host",
            "-e",
            "http_proxy=" + a.proxy,
            "-e",
            "https_proxy=" + a.proxy,
        ]
    mounts = [
        (env / "Runtime/l4v", "/sel4-project/verification/l4v", True),
        (pathlib.Path(a.isabelle).resolve(), "/workspace/verification/isabelle", True),
        (
            pathlib.Path(a.isabelle).resolve(),
            "/sel4-project/verification/isabelle",
            True,
        ),
        (env / "Semantic-Base/workspace", "/semantic-base", True),
        (env / "Semantic-Base/heaps", "/trusted-heaps", True),
        (env / "Semantic-Base/heaps", "/root/.isabelle/heaps", True),
        (env / "Environment-Dataflow", "/public", True),
        (repo / "src", "/vero-src", True),
        (benchmark, "/benchmark", True),
        (pathlib.Path(a.codex).resolve().parent, "/opt/vero", True),
        (pathlib.Path(a.auth).resolve(), "/run/vero-auth", True),
        (run / "sandbox", "/sandbox", False),
        (run / "results", "/results", False),
        (run / "control", "/control", True),
        (run / "agent-home", "/home/agent", False),
    ]
    for source, target, ro in mounts:
        if not source.exists():
            raise FileNotFoundError(str(source))
        cmd += [
            "--mount",
            "type=bind,src="
            + str(source)
            + ",dst="
            + target
            + (",readonly" if ro else ""),
        ]
    cmd += ["--entrypoint", "/bin/bash", a.image, "-c", "sleep infinity"]
    call(*cmd)
    init = r"""set -eu
useradd -u 1001 -M -s /bin/bash agent
useradd -u 1002 -M -s /bin/bash checker
mkdir -p /home/agent/.isabelle/etc /home/agent/.codex /home/checker/.isabelle/etc
cp /root/.isabelle/etc/settings /home/agent/.isabelle/etc/settings
cp /root/.isabelle/etc/settings /home/checker/.isabelle/etc/settings
ln -sfn /root/.isabelle/contrib /home/agent/.isabelle/contrib
ln -sfn /root/.isabelle/contrib /home/checker/.isabelle/contrib
ln -sfn /trusted-heaps /home/agent/.isabelle/heaps
ln -sfn /trusted-heaps /home/checker/.isabelle/heaps
cp /run/vero-auth /home/agent/.codex/auth.json
chmod 600 /home/agent/.codex/auth.json
chown -hR 1001:1001 /home/agent /sandbox
chown -hR 1002:1002 /home/checker
chown 0:1002 /results
chmod 750 /results
chmod 700 /home/checker
find /root -mindepth 1 -maxdepth 1 -type f -exec chmod 600 {} +
find /root -mindepth 1 -maxdepth 1 -type d -exec chmod 700 {} +
chmod 711 /root /root/.isabelle
find /root/.isabelle -mindepth 1 -maxdepth 1 -type f -exec chmod 600 {} +
chmod 700 /root/.isabelle/etc
ln -sfn /opt/vero/rg /usr/local/bin/rg
mkdir -p /backend
ln -sfn /vero-src/vero/isabelle/tool.py /backend/tool.py
"""
    call("docker", "exec", a.name, "bash", "-c", init)
    call(
        "docker",
        "exec",
        a.name,
        "python3",
        "-m",
        "vero.isabelle",
        "render",
        "--benchmark",
        "/benchmark/benchmark.json",
        "--output",
        "/sandbox",
    )
    call("docker", "exec", a.name, "chown", "-R", "1001:1001", "/sandbox")
    print(json.dumps({"container": a.name, "run_root": str(run), "agent_uid": uid}))


if __name__ == "__main__":
    main()
