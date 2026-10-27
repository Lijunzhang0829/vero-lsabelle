"""Content-bound native prefix evidence and complete-theory seals.

The cache is supervisor-owned. Every hit binds the rendered source, transitive
imports, prover configuration and checker implementation; failed checks never hit.
A prefix certificate is deliberately different from a complete theory seal.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .benchmark import digest, read, save
from .planning import dependencies, module_order
from .run_metrics import requires_build


class IncrementalChecks:
    def __init__(self, root, checker):
        self.root = Path(root)
        self.checker = checker
        self.root.mkdir(parents=True, exist_ok=True)
        self.binding = digest(
            {
                "config": getattr(checker, "config", {}),
                "implementation": {
                    p.name: p.read_text()
                    for p in Path(__file__).parent.iterdir()
                    if p.suffix in {".py", ".thy", ".ML", ".json"}
                },
            }
        )
        self.current = {}
        self.seals = {}

    def key(self, source, effective, module):
        names = dependencies(effective, module) | {module, "Vero_Support"}
        return digest(
            {
                "binding": self.binding,
                "sources": {
                    n: (source / (n + ".thy")).read_text() for n in sorted(names)
                },
            }
        )

    def check(self, source, effective, module, pending, statuses):
        source = Path(source)
        key = self.key(source, effective, module)
        path = self.root / "prefix" / (key + ".json")
        record = read(path) if path.exists() else None
        if record and record.get("key") == key and record["result"]["status"] == "ok":
            result = {
                **record["result"],
                "cache_hit": True,
                "cache_evidence": str(path),
                "elapsed_seconds": 0.0,
            }
        else:
            # Keep the owner's native name so imported references remain exact.
            # Only the owner is truncated; imported facts are actually loaded by
            # Isabelle, not replaced with declarations or assumed theorems.
            directory = source.parent / "prefix" / module
            shutil.copytree(source, directory)
            contracts = [c for c in effective["contracts"] if c["module"] == module]
            text = (directory / (module + ".thy")).read_text()
            if pending:
                last = pending[-1]
                polarity = statuses[last["id"]]["polarity"]
                marker = f"(* VERO END {last['id']} {polarity} *)"
                text = text[: text.index(marker) + len(marker)]
                depth = len(last["context"])
            else:
                first = contracts[0]
                marker = f"(* VERO START {first['id']} prove_aux *)"
                if marker not in text:
                    marker = f"vero_prove {first['id']}"
                text = text[: text.index(marker)]
                depth = len(first["context"])
            text += "\n" + "\n".join(["end"] * (depth + 1)) + "\n"
            (directory / (module + ".thy")).write_text(text)
            result = {
                **self.checker.check(directory, module),
                "cache_hit": False,
                "source_directory": str(directory),
                "check_kind": "prefix",
            }
            if result["status"] == "ok" and all(
                c["id"] + ":" + statuses[c["id"]]["polarity"]
                in result.get("audited", [])
                for c in pending
            ):
                save(path, {"key": key, "result": result})
        self.current[module] = {"key": key, "result": result}
        return result

    def _publish_seal(self, module, own, seal):
        events = getattr(self.checker, "events", None)
        if events is not None and seal.get("sealed_at") is not None:
            events.emit(
                "theory_sealed",
                event_id="seal:" + seal["key"],
                timestamp=seal["sealed_at"],
                theory=module,
                contract_ids=sorted(c["id"] for c in own),
                helper_ids=[],
                evidence_id=seal["evidence"],
                seal_key=seal["key"],
                build_required=requires_build(self.checker),
            )

    def seal_complete(self, source, effective, statuses, output):
        """Full checks occur only when every own contract and import is complete.

        A failed seal is separate from a target attempt. It is retried on the
        next evaluation and never published as a successful seal.
        """
        source, output = Path(source), Path(output)
        seals = {}
        imports = effective.get("planning", {}).get("imports", {})
        for module in module_order(effective):
            own = [c for c in effective["contracts"] if c["module"] == module]
            if not all(statuses[c["id"]]["status"] == "proved" for c in own):
                seals[module] = {"status": "incomplete"}
                continue
            if any(
                seals.get(d, {}).get("status") != "sealed"
                for d in imports.get(module, [])
            ):
                seals[module] = {"status": "waiting_for_imports"}
                continue
            key = self.key(source, effective, module)
            path = self.root / "seals" / (key + ".json")
            old = read(path) if path.exists() else None
            if old and old.get("key") == key and old.get("status") == "sealed":
                seals[module] = {**old, "cache_hit": True}
                self._publish_seal(module, own, old)
                continue
            result = self.checker.check(
                source,
                module,
                **(
                    {"timeout": self.checker.config["timeout_seconds"]}
                    if requires_build(self.checker)
                    else {}
                ),
            )
            (output / (module + ".seal.log")).write_text(result["log"])
            audited = set(result.get("audited", []))
            ok = result["status"] == "ok" and all(
                c["id"] + ":prove" in audited for c in own
            )
            build = None
            if ok and requires_build(self.checker):
                build = self.checker.build_session(source, [module])
                (output / (module + ".build.log")).write_text(build.get("log", ""))
                ok = build["status"] == "ok"
            seal = {
                "status": "sealed" if ok else "seal_failed",
                "key": key,
                "cache_hit": False,
                "evidence": str(output / (module + ".seal.log")),
                "result": {k: v for k, v in result.items() if k != "log"},
                "build": {k: v for k, v in build.items() if k != "log"}
                if build
                else None,
            }
            if ok:
                seal["sealed_at"] = time.time()
                save(path, seal)
                self._publish_seal(module, own, seal)
            seals[module] = seal
        self.seals = seals
        save(output / "seals.json", seals)
        return seals
