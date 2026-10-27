"""Content verification of a portable export of the local deletion result."""

import hashlib
from pathlib import Path

from .benchmark import read, save


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(root):
    root = Path(root).resolve()
    manifest = read(root / "portable-manifest.json")
    expected = manifest["files"]
    observed = set()
    for top in [
        "Runtime/l4v",
        "Semantic-Base/workspace",
        "Semantic-Base/heaps",
        "Environment-Dataflow",
    ]:
        for p in (root / top).rglob("*"):
            if p.is_symlink():
                raise ValueError("unexpected symlink: " + str(p))
            if p.is_file():
                observed.add(str(p.relative_to(root)))
    if observed != set(expected):
        raise ValueError("environment file inventory mismatch")
    for name, entry in expected.items():
        p = root / name
        if p.stat().st_size != entry["bytes"] or file_sha(p) != entry["sha256"]:
            raise ValueError("environment content mismatch: " + name)
    for name in (root / "Semantic-Base/heaps").rglob("*"):
        if name.name in {"Refine", "CRefine", "Refine.db", "CRefine.db"}:
            raise ValueError("forbidden target heap " + str(name))
    index = read(root / "Environment-Dataflow/environment-index.json")
    if index != manifest["dataflow"]:
        raise ValueError("dataflow identity mismatch")
    for stream in index["streams"].values():
        if file_sha(root / stream["path"]) != stream["sha256"]:
            raise ValueError("dataflow stream mismatch")
    result = {
        "status": "verified",
        "files": len(expected),
        "dataflow_id": index["dataflow_id"],
        "environment_gate_id": index["environment_gate_id"],
        "counts": index["counts"],
        "portable_manifest_sha256": file_sha(root / "portable-manifest.json"),
    }
    save(root / "portable-verification.json", result)
    return result
