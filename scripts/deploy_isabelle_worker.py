#!/usr/bin/env python3
"""Deploy a sanitized, unprivileged agent and separate privileged evaluator.

Only run against an environment whose public data, runtime and heaps have been
verified. This script never mounts the original seL4 checkout in the worker.
"""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess

CODEX_PROFILES = {"codex_freeform", "codex_repository", "codex_repository_resource_v2"}
RUN_DIRECTORIES = ("sandbox", "results", "control", "agent-home")


def require_fresh_run(run):
    """Reject historical work, including nested sources and old CLI sessions."""
    if run.is_symlink() or (run.exists() and not run.is_dir()):
        raise ValueError("fresh experiment run root must be a real directory")
    if not run.exists():
        return
    for entry in run.iterdir():
        if (
            entry.name not in RUN_DIRECTORIES
            or entry.is_symlink()
            or not entry.is_dir()
            or any(entry.iterdir())
        ):
            raise ValueError(
                "fresh experiment run root contains existing content: " + entry.name
            )


def runtime_identity(image, runtime):
    """Pin the installed image and CLI bytes without inspecting credentials."""
    image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Docker did not return an immutable image ID")
    version = subprocess.run(
        [str(runtime / "codex"), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()
    return {
        "image_requested": image,
        "image_id": image_id,
        "codex_version": version,
        "runtime_sha256": {
            name: hashlib.sha256((runtime / name).read_bytes()).hexdigest()
            for name in ("codex", "codex-code-mode-host", "rg")
        },
    }


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
    p.add_argument("--model", default="gpt-6-astra")
    p.add_argument(
        "--instruction-profile",
        choices=[
            "vero",
            "codex_freeform",
            "codex_repository",
            "codex_repository_resource_v2",
        ],
        default="vero",
    )
    p.add_argument(
        "--checker-backend",
        choices=["a-shared-incremental-v1", "a-shared-batch-v2", "fresh-process-v1"],
        default=None,
    )
    p.add_argument("--command-seconds", type=int, default=60)
    p.add_argument("--dependency-seconds", type=int, default=600)
    p.add_argument(
        "--check-seconds", type=int, default=300, help="Owner-theory limit in seconds"
    )
    p.add_argument(
        "--check-total-seconds",
        type=int,
        default=None,
        help="Whole native check cap; default shared A profile has no combined cap, legacy uses 300s",
    )
    p.add_argument(
        "--resource-config", help="Fully bound and verified resource-v2 configuration"
    )
    p.add_argument("--run-root", required=True)
    p.add_argument("--name", default="vero-isabelle-refine")
    p.add_argument("--image", default="sel4-public:tuned")
    p.add_argument(
        "--agent-seconds",
        type=int,
        default=None,
        help="Optional total agent budget; default: no total limit",
    )
    p.add_argument(
        "--checkpoint-seconds",
        type=int,
        default=600,
        help="Per-call checkpoint interval, not a total limit",
    )
    p.add_argument(
        "--max-contract-retries",
        type=int,
        default=5,
        help="Retries after the first candidate; default: 5 (six attempts total)",
    )
    p.add_argument(
        "--sample-seconds",
        type=int,
        default=120,
        help="Passive snapshot interval; never interrupts the agent",
    )
    p.add_argument(
        "--full-checks",
        action="store_true",
        help="Use legacy full replay rather than incremental prefix checks",
    )
    p.add_argument("--proxy", help="HTTP proxy reachable on the worker host loopback")
    a = p.parse_args()
    resource_mode = a.instruction_profile == "codex_repository_resource_v2"
    resource_config = None
    if resource_mode:
        if not a.resource_config:
            p.error("resource-v2 requires --resource-config; no legacy defaults")
        resource_config = json.loads(pathlib.Path(a.resource_config).read_text())
        from vero.isabelle.resource_runtime import validate_config

        validate_config(resource_config)
        if (
            resource_config["run_id"] != a.name
            or resource_config["agent"]["model"] != a.model
        ):
            p.error(
                "resource config run/model identity differs from requested deployment"
            )
    elif a.resource_config:
        p.error("resource-config requires resource-v2 profile")
    if a.checker_backend is None:
        a.checker_backend = (
            resource_config["checker"]["backend"]
            if resource_mode
            else "a-shared-incremental-v1"
        )
    if resource_mode and (
        a.checker_backend != "a-shared-batch-v2"
        or a.check_seconds != 300
        or a.check_total_seconds not in (None, 0)
        or a.command_seconds != 60
        or a.dependency_seconds != 600
        or a.full_checks
        or a.agent_seconds is not None
        or a.max_contract_retries != 5
    ):
        p.error(
            "resource-v2 checker/agent policy comes from its frozen config; conflicting legacy flags are forbidden"
        )
    private_protocol_mounts = []
    shared = a.checker_backend in {"a-shared-incremental-v1", "a-shared-batch-v2"}
    batch = a.checker_backend == "a-shared-batch-v2"
    if a.check_total_seconds is None:
        a.check_total_seconds = 0 if shared else 300
    if shared and a.full_checks:
        p.error(
            "full-checks is only for explicit legacy replay; shared backend requires full seals"
        )
    if a.command_seconds <= 0:
        p.error("command-seconds must be positive")
    if not a.model.strip():
        p.error("model must be an explicit nonempty identifier")
    if a.check_seconds <= 0 or a.dependency_seconds <= 0 or a.check_total_seconds < 0:
        p.error("checker phase limits must be positive and total limit nonnegative")
    if a.checkpoint_seconds <= 0 or (
        a.agent_seconds is not None and a.agent_seconds <= 0
    ):
        p.error("time limits must be positive")
    if a.dependency_seconds <= 0:
        p.error("dependency-seconds must be positive")
    if (
        a.instruction_profile in CODEX_PROFILES
        and not resource_mode
        and (a.agent_seconds is not None or a.max_contract_retries != 5)
    ):
        p.error("autonomous baseline requires unlimited total time and five retries")
    if a.instruction_profile == "codex_repository" and (
        (a.check_seconds, a.check_total_seconds, a.dependency_seconds)
        != (300, 0 if shared else 300, 600)
        or (shared and a.command_seconds != 60)
        or a.full_checks
    ):
        p.error(
            "repository experiment requires the frozen shared A or legacy checker profile"
        )
    if a.sample_seconds <= 0:
        p.error("sample-seconds must be positive")
    if a.max_contract_retries < 0:
        p.error("max-contract-retries must be nonnegative")
    repo = pathlib.Path(a.repo).resolve()
    if resource_mode:
        from vero.isabelle.resource_protocol import validate_facade_source_root

        manifest_path = pathlib.Path(
            resource_config["verification"]["tool_protocol_manifest"]
        )
        validate_facade_source_root(json.loads(manifest_path.read_text()), repo / "src")
    env = pathlib.Path(a.environment).resolve()
    benchmark = (
        pathlib.Path(a.benchmark).resolve()
        if a.benchmark
        else repo / "benchmarks/RefineIsabelleV2"
    )
    run = pathlib.Path(a.run_root).absolute()
    if a.instruction_profile in CODEX_PROFILES:
        require_fresh_run(run)
    run = run.resolve()
    runtime = pathlib.Path(a.codex).resolve().parent
    for companion in ["codex", "codex-code-mode-host", "rg"]:
        if not (runtime / companion).is_file():
            raise FileNotFoundError("missing Codex runtime companion: " + companion)
    identity = (
        runtime_identity(a.image, runtime)
        if a.instruction_profile in CODEX_PROFILES
        else None
    )
    benchmark_bytes = (benchmark / "benchmark.json").read_bytes()
    environment_identity = hashlib.sha256(
        (env / "portable-manifest.json").read_bytes()
    ).hexdigest()
    for key in RUN_DIRECTORIES:
        (run / key).mkdir(parents=True, exist_ok=True)
    if shared or a.instruction_profile == "codex_repository":
        # Project only fixed input data. Never mount old benchmark-side artifacts
        # or mutate a shared benchmark's checker configuration.
        benchmark = run / "control" / "benchmark"
        benchmark.mkdir()
        (benchmark / "benchmark.json").write_bytes(benchmark_bytes)
    uid = 1001
    shared_backend_directory = None
    checker = {
        "backend": a.checker_backend,
        "incremental": not a.full_checks,
        "environment_identity": environment_identity,
        "session": "Semantic_Base",
        "isabelle": "/workspace/verification/isabelle/bin/isabelle",
        "timeout_seconds": a.check_seconds,
        "total_timeout_seconds": a.check_total_seconds or None,
        "dependency_timeout_seconds": None if shared else a.dependency_seconds,
        "threads": 4 if batch or not shared else 8,
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
    if shared:
        manifest = json.loads(
            (
                repo
                / "src/vero/isabelle"
                / ("a_batch_core.json" if batch else "a_shared_core.json")
            ).read_text()
        )
        for name, entry in manifest["files"].items():
            if (
                hashlib.sha256(
                    (repo / "src/vero/isabelle" / name).read_bytes()
                ).hexdigest()
                != entry["sha256"]
            ):
                raise ValueError("shared core provenance mismatch: " + name)
        # Keep native requests, full checker output and build evidence when the
        # container is removed. This private directory must never follow an old
        # run's symlink or reuse its existing evidence.
        if (run / "results").is_symlink():
            raise ValueError("shared backend results directory must not be a symlink")
        shared_backend_directory = run / "results/shared-native"
        shared_backend_directory.mkdir(mode=0o700)
        checker.update(
            shared_backend_root="/home/checker/shared-backend",
            source_qualifier="Generated_Refine",
            shared_core_sha256=hashlib.sha256(
                json.dumps(manifest, sort_keys=True).encode()
            ).hexdigest(),
            command_timeout_seconds=a.command_seconds,
            initial_statement_timeout_seconds=1800,
            helper_statement_timeout_seconds=900,
            integration_build_timeout_seconds=900,
            build_extra_sessions=["Lib", "CorresK", "AInvs"],
        )
        checker["env"]["ISABELLE_IDENTIFIER"] = "vero-shared"
        if batch:
            checker.update(
                timeout_seconds=900,
                command_timeout_seconds=900,
                candidate_timeout_seconds=120,
                initial_statement_timeout_seconds=900,
            )
    (benchmark / "checker.json").write_text(json.dumps(checker, indent=2) + "\n")
    config = {
        "instruction_profile": a.instruction_profile,
        "sandbox": "/sandbox",
        "sandbox_uid": uid,
        "max_iterations": None,
        "iteration_time_chunk_seconds": a.checkpoint_seconds,
        "iteration_max_time_seconds": None,
        "completion_metric": "positive",
        "total_agent_seconds": a.agent_seconds,
        "feedback": True,
        "resume_artifact": False,
        "agent": {
            "codex": "/opt/vero/codex",
            "model": a.model,
            "sandbox_mode": "danger-full-access",
            "external_isolation": "docker-nonroot",
            "reasoning_effort": "medium",
            "timeout_seconds": a.checkpoint_seconds,
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
    config["metrics"] = {
        "directory": "/results/generation/metrics",
        "run_id": a.name,
        "manifest": {
            "method": a.instruction_profile,
            "benchmark_sha256": hashlib.sha256(benchmark_bytes).hexdigest(),
            "environment_identity": environment_identity,
            "checker_backend": a.checker_backend,
            "runtime_identity": identity,
            "lineage": {"fresh": True, "parent_run": None},
        },
    }
    config["agent"]["metrics"] = config["metrics"]
    if a.agent_seconds is None:
        config["max_contract_retries"] = a.max_contract_retries
        config["sample_interval_seconds"] = a.sample_seconds
        config["iteration_time_chunk_seconds"] = None
        config["agent"]["timeout_seconds"] = None
    if a.instruction_profile in {"codex_repository", "codex_repository_resource_v2"}:
        config["agent"]["codex"] = "/backend/codex_transport.py"
        config["reference_directory"] = "/results/reference"
    if resource_mode:
        # Preserve explicit v2 policy while binding actual deployment paths and
        # shared checker settings. No max_contract_retries enters the new schema.
        from vero.isabelle.resource_runtime import validate_config

        generated = config
        resource_config["resources"] = {
            **resource_config["resources"],
            "isolated_container_id": a.name,
        }
        config = {
            **resource_config,
            "runtime_identity": identity,
            "sandbox": "/sandbox",
            "sandbox_uid": uid,
            "reference_directory": "/results/reference",
            "agent": {
                **resource_config["agent"],
                **generated["agent"],
                "timeout_seconds": None,
            },
            "checker": {**resource_config["checker"], **generated["checker"]},
            "metrics": {**resource_config["metrics"], **generated["metrics"]},
        }
        config["agent"]["reasoning_effort"] = resource_config["agent"][
            "reasoning_effort"
        ]
        config["agent"]["metrics"] = config["metrics"]
        validate_config(config)
        verification = dict(config["verification"])
        roots = verification["protocol_source_roots"]
        for key, source in roots.items():
            private_protocol_mounts.append(
                (pathlib.Path(source), "/protocol-private/" + key, True)
            )
        private_protocol_mounts.append(
            (
                pathlib.Path(verification["tool_protocol_manifest"]),
                "/protocol-private/manifest.json",
                True,
            )
        )
        verification["protocol_source_roots"] = {
            key: "/protocol-private/" + key for key in roots
        }
        verification["tool_protocol_manifest"] = "/protocol-private/manifest.json"
        config["verification"] = verification
    if identity is not None:
        config["runtime_identity"] = identity
        (run / "control/preparation.json").write_text(
            json.dumps(
                {
                    "schema": "codex-deployment-preparation-v1",
                    "instruction_profile": a.instruction_profile,
                    "runtime_identity": identity,
                    "benchmark_sha256": hashlib.sha256(benchmark_bytes).hexdigest(),
                    "environment_identity": environment_identity,
                    "checker": checker,
                    "model": a.model,
                    "reasoning_effort": config["agent"]["reasoning_effort"],
                },
                indent=2,
            )
            + "\n"
        )
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
    if resource_mode:
        resources = config["resources"]
        cmd += [
            "--hostname",
            a.name,
            "--memory",
            str(resources["memory_limit_bytes"]),
            "--cpus",
            str(resources["cpu_allocation"]),
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
    mounts.extend(private_protocol_mounts)
    if shared_backend_directory is not None:
        mounts.append((shared_backend_directory, "/home/checker/shared-backend", False))
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
    cmd += [
        "--entrypoint",
        "/bin/bash",
        identity["image_id"] if identity else a.image,
        "-c",
        "sleep infinity",
    ]
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
mkdir -p /home/checker/.isabelle/vero-shared/etc /home/checker/shared-heaps /home/checker/shared-backend
cp /root/.isabelle/etc/settings /home/checker/.isabelle/vero-shared/etc/settings
cat >> /home/checker/.isabelle/vero-shared/etc/settings <<'SETTINGS'
ISABELLE_HEAPS="$USER_HOME/shared-heaps"
ISABELLE_HEAPS_SYSTEM="/trusted-heaps"
SETTINGS
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
    if shared_backend_directory is not None:
        # chown above applies through the bind mount; both paths to the same
        # evidence (/results/shared-native and this mount) remain checker-only.
        init += "chmod 700 /home/checker/shared-backend\n"
    if resource_mode:
        # The unprivileged agent can use only the metered socket. Isabelle and
        # native runtime sources are accessible to checker/root, not agent UID.
        init += "chown 0:1002 /workspace/verification /sel4-project/verification\nchmod 750 /workspace/verification /sel4-project/verification\nchown 0:0 /protocol-private\nchmod 700 /protocol-private\n"
    if resource_mode:
        init += "chown 0:0 /protocol-private\nchmod 700 /protocol-private\n"
    call("docker", "exec", a.name, "bash", "-c", init)
    if resource_mode:
        call(
            "docker",
            "exec",
            a.name,
            "python3",
            "-m",
            "vero.isabelle.resource_runtime",
            "validate-config",
            "--config",
            "/control/config.json",
        )
    if a.instruction_profile in CODEX_PROFILES:
        call(
            "docker",
            "exec",
            a.name,
            "cp",
            "/vero-src/vero/isabelle/freeform_tool.py",
            "/backend/freeform_tool.py",
        )
        if a.instruction_profile in {
            "codex_repository",
            "codex_repository_resource_v2",
        }:
            call(
                "docker",
                "exec",
                a.name,
                "cp",
                "/vero-src/vero/isabelle/repository_tool.py",
                "/backend/repository_tool.py",
            )
            call(
                "docker",
                "exec",
                a.name,
                "cp",
                "/vero-src/vero/isabelle/codex_transport.py",
                "/backend/codex_transport.py",
            )
            call(
                "docker", "exec", a.name, "chmod", "755", "/backend/codex_transport.py"
            )
        print(json.dumps({"container": a.name, "run_root": str(run), "agent_uid": uid}))
        return
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
