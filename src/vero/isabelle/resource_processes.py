"""Run-owned subprocess identities registered durably before executable startup."""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def process_identity(pid):
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        return {
            "pid": int(pid),
            "start_ticks": fields[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "state": fields[0],
        }
    except (FileNotFoundError, ProcessLookupError):
        return None


def identity_key(identity):
    return ":".join(str(identity[k]) for k in ("boot_id", "pid", "start_ticks"))


def is_alive(identity):
    actual = process_identity(identity["pid"])
    return (
        actual is not None
        and actual["state"] != "Z"
        and identity_key(actual) == identity_key(identity)
    )


def _pidfd_call(name, number, *args):
    """Some Python builds omit pidfd wrappers despite a capable Linux kernel."""
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, name, None)
    if function is not None:
        result = function(*args)
    else:
        if platform.machine() not in {"x86_64", "aarch64", "riscv64"}:
            raise RuntimeError("no supported identity-safe pidfd syscall ABI")
        result = libc.syscall(number, *args)
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def _pidfd_open(pid):
    native = getattr(os, "pidfd_open", None)
    return native(pid) if native else _pidfd_call("pidfd_open", 434, int(pid), 0)


def _pidfd_signal(descriptor, sig):
    native = getattr(signal, "pidfd_send_signal", None)
    if native:
        return native(descriptor, sig)
    return _pidfd_call(
        "pidfd_send_signal", 424, descriptor, int(sig), ctypes.c_void_p(), 0
    )


def kill_identity_tree(identity):
    """Pin the root with a pidfd before stopping/enumerating its descendants."""
    try:
        descriptor = _pidfd_open(identity["pid"])
    except ProcessLookupError:
        return
    try:
        if not is_alive(identity):
            return
        try:
            _pidfd_signal(descriptor, signal.SIGSTOP)
        except ProcessLookupError:
            return
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = path.read_text().rsplit(")", 1)[1].split()
                if int(fields[1]) == identity["pid"]:
                    child = process_identity(int(path.parent.name))
                    if child is not None:
                        kill_identity_tree(child)
            except (FileNotFoundError, ProcessLookupError):
                pass
        try:
            _pidfd_signal(descriptor, signal.SIGKILL)
        except ProcessLookupError:
            pass
    finally:
        os.close(descriptor)


class ResourceProcessRegistry:
    def __init__(
        self,
        path,
        run_id,
        *,
        owner_pid=None,
        owner_key=None,
        confined_uids=(),
        isolated_container_id=None,
    ):
        self.path = Path(path).resolve()
        self.run_id = run_id
        if not run_id:
            raise ValueError("run identity required")
        scope = self._validate_scope(confined_uids, isolated_container_id)
        with self._transaction() as data:
            if not data:
                data.update(
                    schema_version=1,
                    run_id=run_id,
                    owners={},
                    processes={},
                    closed_owners={},
                    events=[],
                )
            if data.get("run_id") != run_id or data.get("schema_version") != 1:
                raise ValueError("process registry identity mismatch")
            if owner_key is None:
                owner = process_identity(
                    os.getpid() if owner_pid is None else owner_pid
                )
                if owner is None or owner["state"] == "Z":
                    raise ValueError("supervisor must be alive")
                self.owner_key = identity_key(owner)
                owner.update(scope)
                previous = data["owners"].setdefault(self.owner_key, owner)
                if any(previous.get(k) != v for k, v in scope.items()):
                    raise ValueError("supervisor confinement changed")
            else:
                if owner_key not in data["owners"]:
                    raise ValueError("unknown supervisor identity")
                self.owner_key = owner_key

    @staticmethod
    def _validate_scope(uids, container_id):
        if not uids:
            if container_id is not None:
                raise ValueError("container identity requires explicit confined UIDs")
            return {}
        if any(type(uid) is not int for uid in uids) or sorted(uids) != [1001, 1002]:
            raise ValueError(
                "production process confinement requires exactly UIDs 1001 and 1002"
            )
        if os.geteuid() != 0 or not Path("/.dockerenv").is_file():
            raise ValueError(
                "UID cleanup requires root in the dedicated Docker container"
            )
        if (
            not isinstance(container_id, str)
            or not container_id
            or Path("/etc/hostname").read_text().strip() != container_id
        ):
            raise ValueError(
                "dedicated container identity differs from frozen deployment"
            )
        return {"confined_uids": list(uids), "isolated_container_id": container_id}

    def _confined_identities(self):
        owner = self.snapshot()["owners"][self.owner_key]
        uids = owner.get("confined_uids", [])
        if not uids:
            return []
        self._validate_scope(uids, owner.get("isolated_container_id"))
        identities = []
        for status in Path("/proc").glob("[0-9]*/status"):
            try:
                row = next(
                    line
                    for line in status.read_text().splitlines()
                    if line.startswith("Uid:")
                )
                if set(map(int, row.split()[1:])) & set(uids):
                    identity = process_identity(int(status.parent.name))
                    if identity is not None and is_alive(identity):
                        identities.append(identity)
            except (FileNotFoundError, ProcessLookupError, StopIteration):
                continue
        return identities

    @contextmanager
    def _transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            before = self.path.read_text() if self.path.exists() else "{}"
            data = json.loads(before)
            yield data
            encoded = json.dumps(data, sort_keys=True) + "\n"
            if encoded != before:
                fd, temporary = tempfile.mkstemp(
                    prefix=self.path.name, dir=self.path.parent
                )
                try:
                    with os.fdopen(fd, "w") as stream:
                        stream.write(encoded)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, self.path)
                    directory = os.open(self.path.parent, os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)

    def snapshot(self):
        with self._transaction() as data:
            return json.loads(json.dumps(data))

    def owner_alive(self):
        return is_alive(self.snapshot()["owners"][self.owner_key])

    def bind_budget(self, budget_path):
        with self._transaction() as data:
            data["owners"][self.owner_key]["budget_path"] = str(
                Path(budget_path).resolve()
            )

    def _admit(self, data):
        if self.owner_key in data.get("closed_owners", {}):
            raise RuntimeError("supervisor process scope has closed")
        owner = data["owners"][self.owner_key]
        if not is_alive(owner):
            raise RuntimeError("supervisor exited before executable startup")
        if owner.get("budget_path"):
            budget = json.loads(Path(owner["budget_path"]).read_text())
            stage = budget["stages"][-1]
            if (
                budget["run_id"] != self.run_id
                or stage["state"] != "active"
                or time.time() >= stage["deadline_at"]
            ):
                raise RuntimeError("resource stage expired before executable startup")

    def register_self(self, kind, command):
        if kind not in ("model", "prover", "build"):
            raise ValueError("unknown process kind")
        current = process_identity(os.getpid())
        with self._transaction() as data:
            self._admit(data)
            key = identity_key(current)
            data["processes"][key] = {
                **current,
                "kind": kind,
                "owner_key": self.owner_key,
                "registered_at": time.time(),
                "command_sha256": hashlib.sha256(
                    json.dumps(command).encode()
                ).hexdigest(),
            }
            data["events"].append(
                {"kind": "process_registered", "key": key, "at": time.time()}
            )
        return current

    def popen(self, command, *, kind, **kwargs):
        """No requested command executes until its process identity is persisted."""
        if not isinstance(command, (list, tuple)) or not command or kwargs.get("shell"):
            raise ValueError(
                "registered subprocess requires an argv list without shell=True"
            )
        if kind not in ("model", "prover", "build"):
            raise ValueError("unknown process kind")
        with self._transaction() as data:
            self._admit(data)
        argv = [
            sys.executable,
            "-m",
            "vero.isabelle.resource_processes",
            "--registry",
            str(self.path),
            "--run-id",
            self.run_id,
            "--owner-key",
            self.owner_key,
            "--kind",
            kind,
            "--",
            *map(str, command),
        ]
        env = dict(kwargs.pop("env", os.environ))
        package_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = package_root + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        return subprocess.Popen(argv, env=env, **kwargs)

    def cleanup(self, reason):
        """Kill verified owned roots and descendants; never trust PID alone."""
        with self._transaction() as data:
            data["closed_owners"][self.owner_key] = {
                "reason": reason,
                "at": time.time(),
            }
            records = json.loads(json.dumps(data["processes"]))
        outcomes = []
        for key, record in records.items():
            if record["owner_key"] != self.owner_key:
                continue
            alive = is_alive(record)
            if alive:
                kill_identity_tree(record)
            outcomes.append(
                {
                    "key": key,
                    "pid": record["pid"],
                    "action": "killed"
                    if alive
                    else "already_exited_or_identity_changed",
                }
            )
        # Dedicated production containers reserve these UIDs for this run.
        # Repeat after killing roots to catch daemonized/reparented descendants.
        for _ in range(100):
            remaining = self._confined_identities()
            if not remaining:
                break
            for identity in remaining:
                kill_identity_tree(identity)
                outcomes.append(
                    {
                        "key": identity_key(identity),
                        "pid": identity["pid"],
                        "action": "killed_confined_uid",
                    }
                )
            time.sleep(0.01)
        else:
            raise RuntimeError("confined UID process scope failed to become quiescent")
        with self._transaction() as data:
            data["events"].append(
                {
                    "kind": "process_cleanup",
                    "at": time.time(),
                    "reason": reason,
                    "owner_key": self.owner_key,
                    "outcomes": outcomes,
                }
            )
        return outcomes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--owner-key", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("executable required")
    registry = ResourceProcessRegistry(
        args.registry, args.run_id, owner_key=args.owner_key
    )
    registry.register_self(args.kind, command)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
