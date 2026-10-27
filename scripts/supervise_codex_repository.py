#!/usr/bin/env python3
"""Detached, identity-bound supervision of one fresh repository experiment."""

import argparse
import collections
import fcntl
import hashlib
import inspect
import json
import os
import subprocess
import time
from pathlib import Path

TRANSIENT = (
    "stream disconnected",
    "connection",
    "tls handshake",
    "timed out",
    "timeout",
    "model is at capacity",
    "temporarily unavailable",
    "error sending request",
    "failed to send request",
    "502",
    "503",
    "429",
)


def save(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def summarize_nodes(state):
    nodes = state.get("helper_stages", {}).get("nodes", {})
    groups = {}
    retry_requests = []
    for kind in ("contract", "helper"):
        rows = [n for n in nodes.values() if n["kind"] == kind]
        counts = collections.Counter()
        for row in rows:
            blockers = any(not nodes[d]["green"] for d in row["dependencies"])
            history = (
                state.get("contracts", {})
                .get(row.get("node_id", ""), {})
                .get("history", [])
            )
            last = history[-1] if history else {}
            recoverable = (
                not row["green"]
                and last.get("recoverable")
                and last.get("infrastructure_retry", 0) < 2
            )
            if recoverable:
                retry_requests.append(last["request_id"])
            status = (
                "proved"
                if row["green"]
                else "checking"
                if row.get("pending_request")
                else "recovery_pending"
                if recoverable and row["stage_attempts"] >= 6
                else "blocked"
                if blockers
                else "exhausted"
                if row["stage_attempts"] >= 6
                else "ready"
            )
            counts[status] += 1
        groups[kind + "s"] = {
            "total": len(rows),
            "statuses": dict(counts),
            "cumulative_attempts": sum(r["cumulative_attempts"] for r in rows),
        }
    groups["stage_credits"] = sum(
        e["kind"] == "stage_credit"
        for e in state.get("helper_stages", {}).get("events", [])
    )
    groups["retry_requests"] = sorted(set(retry_requests))
    groups["pending"] = state.get("pending")
    groups["finalized"] = state.get("finalized", False)
    groups["theory_seals"] = dict(
        collections.Counter(
            str(r.get("status", "unknown"))
            for r in state.get("theory_seals", {}).values()
        )
    )
    return groups


def error_messages(events):
    result = []
    for event in events:
        if (
            event.get("type") in ("error", "turn.failed")
            or event.get("item", {}).get("type") == "error"
        ):
            result.append(json.dumps(event)[:1500])
    return result


def retry_allowed(status, messages, restarts, limit):
    return (
        status == "agent_error"
        and restarts < limit
        and any(word in "\n".join(messages).lower() for word in TRANSIENT)
    )


def verify_mounts(meta, root, auth, image):
    mounts = {m["Destination"]: m["Source"] for m in meta["Mounts"]}
    for target, suffix in (
        ("/sandbox", "sandbox"),
        ("/results", "results"),
        ("/control", "control"),
        ("/home/agent", "agent-home"),
    ):
        if mounts.get(target) != str(root / suffix):
            raise ValueError("container does not belong to this run: " + target)
    expected_auth = root.parent.parent / "vero-private" / (root.name + "-auth.json")
    if auth != expected_auth or mounts.get("/run/vero-auth") != str(auth):
        raise ValueError("temporary authentication ownership mismatch")
    if meta["Image"] != image or not meta["State"]["Running"]:
        raise ValueError("worker image/state mismatch")


OBSERVER = (
    """
import collections,json,sys,time
from pathlib import Path
ROOT=Path('/results/generation')
def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default
"""
    + inspect.getsource(summarize_nodes)
    + inspect.getsource(error_messages)
    + """
state=read(ROOT/'contract-retries/state.json', {})
journal=read(ROOT/'iterations.json', {})
result={'at':time.time(),'journal_status':journal.get('status','not_started'),**summarize_nodes(state)}
result['iterations']=len(journal.get('iterations',[]))
result['binding_present']=(ROOT/'binding.json').exists()
result['reference']=read(Path('/results/reference/reference.json'), {})
result['reference']={k:(len(v) if k=='terms' else v) for k,v in result['reference'].items()}
result['preflight']=read(Path('/results/preflight/finished.json'), {})
result['probe']=read(Path('/results/preflight/model-availability.json'), {})
result['runner']=read(Path(sys.argv[1]), {}) if len(sys.argv)>1 else {}
result['errors']=[]
if journal.get('status')=='agent_error' and journal.get('iterations'):
    latest=journal['iterations'][-1]['iteration']
    path=ROOT/('iter-%03d'%latest)/'agent/agent_events.jsonl'
    events=[]
    if path.exists():
        for line in path.read_text().splitlines():
            try: event=json.loads(line)
            except ValueError: continue
            if event.get('type') in ('error','turn.failed') or event.get('item',{}).get('type')=='error': events.append(event)
    result['errors']=error_messages(events)
paths=sorted(ROOT.glob('iter-*/agent/state.json'))
if paths:
    result['agent_state']=read(paths[-1])
    events=paths[-1].with_name('agent_events.jsonl')
    if events.exists(): result['agent_events']={'bytes':events.stat().st_size,'mtime':events.stat().st_mtime}
print(json.dumps(result))
"""
)

RUNNER = r"""
import fcntl

import json,subprocess,sys,time,traceback
from pathlib import Path
marker=Path(sys.argv[1]); marker.parent.mkdir(exist_ok=True)
def save(data):
    tmp=marker.with_suffix('.tmp'); tmp.write_text(json.dumps(data,indent=2)+'\n'); tmp.replace(marker)

with marker.with_suffix(".lock").open("a") as lock:
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)
    if marker.exists():
        sys.exit(0)
    command=['python3','-m','vero.isabelle.repository','run','--benchmark','/benchmark/benchmark.json','--config','/control/config.json','--output','/results/generation']
    if sys.argv[2]=='resume': command.append('--resume')
    started=time.time(); save({'status':'running','started_at':started,'command':command})
    try:
        with marker.with_suffix('.log').open('w') as log:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
        save({'status':'exited','started_at':started,'finished_at':time.time(),'exit_code':result.returncode,'command':command})
    except BaseException as exc:
        save({'status':'exited','started_at':started,'finished_at':time.time(),'error_type':type(exc).__name__,'error':str(exc),'exit_code':1,'command':command})
"""


def docker_json(cid, program, *args):
    output = subprocess.check_output(
        ["docker", "exec", cid, "python3", "-c", program, *args], text=True, timeout=30
    )
    return json.loads(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--auth-file", required=True)
    parser.add_argument("--retain-auth", action="store_true", help="Retain run credentials until the user approves deletion")
    parser.add_argument("--poll-seconds", type=float, default=20)
    parser.add_argument("--max-restarts", type=int, default=5)
    args = parser.parse_args()
    root, auth = Path(args.run_root).resolve(), Path(args.auth_file).resolve()
    control = root / "control"
    config_path = control / "config.json"
    config = json.loads(config_path.read_text())
    if (
        config["instruction_profile"] != "codex_repository"
        or config["agent"]["reasoning_effort"] != "medium"
    ):
        raise ValueError("wrong experiment profile")
    if args.poll_seconds <= 0 or args.max_restarts < 0:
        raise ValueError("invalid supervision bounds")
    with (control / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (control / "finished.json").exists():
            raise ValueError("this supervisor already finished; preserve the result")
        meta = json.loads(
            subprocess.check_output(["docker", "inspect", args.container], text=True)
        )[0]
        verify_mounts(meta, root, auth, config["runtime_identity"]["image_id"])
        cid = meta["Id"]
        identity = {
            "container_id": cid,
            "run_root": str(root),
            "model": config["agent"]["model"],
            "reasoning_effort": config["agent"]["reasoning_effort"],
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        }
        started_path = control / "supervisor-started.json"
        current_path = control / "supervisor-current.json"
        if started_path.exists():
            if json.loads(started_path.read_text())["identity"] != identity:
                raise ValueError("supervisor resume identity mismatch")
        else:
            initial = docker_json(cid, OBSERVER)
            if (
                initial["binding_present"]
                or initial["preflight"].get("status") != "ok"
                or initial["reference"].get("terms") != 287
                or initial["probe"].get("model") != identity["model"]
                or not initial["probe"].get("ready_response")
            ):
                raise ValueError(
                    "fresh native/model preflight missing or run already started"
                )
            save(started_path, {"identity": identity, "started_at": time.time()})
        current = (
            json.loads(current_path.read_text()) if current_path.exists() else None
        )
        owned = current is not None
        final = {"status": "supervisor_error", "identity": identity}
        try:
            restarts = current["call_index"] if current else 0
            while True:
                if current is None:
                    if (
                        hashlib.sha256(config_path.read_bytes()).hexdigest()
                        != identity["config_sha256"]
                    ):
                        raise ValueError("frozen config changed")
                    marker = f"/results/supervision/call-{restarts:03d}.json"
                    mode = "resume" if restarts else "fresh"
                    subprocess.run(
                        [
                            "docker",
                            "exec",
                            "-d",
                            cid,
                            "python3",
                            "-c",
                            RUNNER,
                            marker,
                            mode,
                        ],
                        check=True,
                        timeout=30,
                    )
                    owned = True
                    current = {
                        "call_index": restarts,
                        "marker": marker,
                        "mode": mode,
                        "launched_at": time.time(),
                        "container_id": cid,
                    }
                    save(current_path, current)
                failures = 0
                while True:
                    try:
                        snapshot = docker_json(cid, OBSERVER, current["marker"])
                        failures = 0
                    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                        failures += 1
                        if failures >= 3:
                            raise
                        time.sleep(min(args.poll_seconds, 20))
                        continue
                    snapshot.update(identity=identity, call_index=restarts)
                    save(control / "heartbeat.json", snapshot)
                    if snapshot["runner"].get("status") == "exited":
                        break
                    time.sleep(args.poll_seconds)
                status = snapshot["journal_status"]
                if not retry_allowed(
                    status, snapshot["errors"], restarts, args.max_restarts
                ):
                    final = {
                        "status": status if status != "running" else "runner_error",
                        "identity": identity,
                        "restarts": restarts,
                        "finished_at": time.time(),
                        "snapshot": snapshot,
                    }
                    break
                save(
                    control / f"transport-restart-{restarts + 1:03d}.json",
                    {
                        "at": time.time(),
                        "previous_snapshot": snapshot,
                        "same_run": True,
                        "candidate_budgets_reset": False,
                    },
                )
                time.sleep(60)
                restarts += 1
                current = None
        except BaseException as exc:  # noqa: BLE001 - persist failure before owned-worker cleanup
            final.update(
                error_type=type(exc).__name__, error=str(exc), finished_at=time.time()
            )
        finally:
            if owned:
                cleanup = []
                try:
                    program = """import os; from pathlib import Path
if REMOVE_AUTH: Path('/home/agent/.codex/auth.json').unlink(missing_ok=True)
for base,dirs,files in os.walk('/results',followlinks=False):
 for name in files+dirs:
  os.chown(os.path.join(base,name),UID,GID,follow_symlinks=False)
os.chown('/results',UID,GID)
""".replace("UID", str(os.getuid())).replace("GID", str(os.getgid())).replace("REMOVE_AUTH", repr(not args.retain_auth))
                    subprocess.run(
                        ["docker", "exec", cid, "python3", "-c", program],
                        check=True,
                        timeout=60,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    cleanup.append(
                        {"step": "results_ownership_and_agent_auth", "error": str(exc)}
                    )
                worker_removed = False
                try:
                    removed = subprocess.run(
                        ["docker", "rm", "-f", cid],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=30,
                    )
                    worker_removed = removed.returncode == 0
                    if removed.returncode:
                        cleanup.append(
                            {"step": "remove_own_worker", "error": removed.stderr[:500]}
                        )
                except (OSError, subprocess.SubprocessError) as exc:
                    cleanup.append({"step": "remove_own_worker", "error": str(exc)})
                try:
                    if not args.retain_auth:
                        auth.unlink(missing_ok=True)
                except OSError as exc:
                    cleanup.append({"step": "remove_own_host_auth", "error": str(exc)})
                final["cleanup"] = {
                    "worker_removed": worker_removed,
                    "host_auth_removed": not auth.exists(),
                    "auth_retained_by_request": args.retain_auth,
                    "errors": cleanup,
                }
            else:
                final["cleanup"] = {"not_owned": True}
            save(control / "finished.json", final)
        print(
            json.dumps(
                {
                    "status": final["status"],
                    "control": str(control),
                    "model": identity["model"],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
