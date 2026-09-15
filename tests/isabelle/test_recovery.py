import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vero.isabelle.benchmark import extract, prepare, render, save
from vero.isabelle.checker import Checker
from vero.isabelle.runner import recover_iteration, run


class RecoveryTests(unittest.TestCase):
    def test_interrupted_call_charged_and_completed_eval_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contracts = root / "input.jsonl"
            contracts.write_text(
                json.dumps(
                    {
                        "contract_id": "t",
                        "name": "t",
                        "owner_theory": "T",
                        "exact_statement": 'lemma t: "True"',
                    }
                )
                + "\n"
            )
            bench = prepare(contracts, root / "bench", base_import="Main")
            workspace = root / "source"
            render(bench, workspace)
            iteration = root / "iter-000"
            save(iteration / "submitted.json", {"max_seconds": 123})
            journal = {"iterations": [], "status": "running"}
            rec = recover_iteration(
                bench, {"checker": {}}, root, iteration, workspace, journal
            )
            self.assertEqual(rec["agent"]["elapsed_seconds"], 123)
            self.assertTrue(rec["agent"]["recovered_interruption"])
            self.assertEqual(len(journal["iterations"]), 1)
            # Recovering the persisted evaluation must not invoke the prover again.
            with patch(
                "vero.isabelle.runner.evaluate",
                side_effect=AssertionError("re-evaluated"),
            ):
                recover_iteration(
                    bench,
                    {"checker": {}},
                    root,
                    iteration,
                    workspace,
                    {"iterations": []},
                )

    def test_continuation_seed_is_bound_and_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contracts = root / "input.jsonl"
            contracts.write_text(
                json.dumps(
                    {
                        "contract_id": "t",
                        "name": "t",
                        "owner_theory": "T",
                        "exact_statement": 'lemma t: "True"',
                    }
                )
                + "\n"
            )
            bench = prepare(contracts, root / "bench", base_import="Main")
            cfg = {
                "sandbox": str(root / "source"),
                "total_agent_seconds": 10,
                "agent": {"timeout_seconds": 10},
                "checker": {},
                "initial_proofs": {"c0001:prove": "by simp"},
            }
            save(root / "config.json", cfg)

            def fake_run(**kwargs):
                artifact = extract(bench, kwargs["sandbox_dir"])
                self.assertEqual(artifact["slots"]["c0001:prove"]["body"], "by simp")
                return {"ok": True, "elapsed_seconds": 1}

            with (
                patch("vero.isabelle.runner.CodexAgent.run", side_effect=fake_run),
                patch(
                    "vero.isabelle.runner.evaluate",
                    return_value={
                        "proved_specs": 1,
                        "disproved_specs": 0,
                        "complete": True,
                    },
                ),
            ):
                result = run(
                    root / "bench/benchmark.json", root / "config.json", root / "trial"
                )
            self.assertEqual(result["status"], "complete")
            cfg["initial_proofs"]["c0001:prove"] = "by auto"
            save(root / "config.json", cfg)
            with self.assertRaisesRegex(ValueError, "configuration changed"):
                run(
                    root / "bench/benchmark.json",
                    root / "config.json",
                    root / "trial",
                    resume=True,
                )

    def test_running_process_cannot_be_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ticks = (
                Path(f"/proc/{os.getpid()}/stat")
                .read_text()
                .rsplit(")", 1)[1]
                .split()[19]
            )
            save(
                root / "agent/state.json",
                {"status": "running", "pid": os.getpid(), "process_start_ticks": ticks},
            )
            with self.assertRaisesRegex(RuntimeError, "still running"):
                recover_iteration({}, {}, root, root, root, {})

    def test_timeout_kills_prover_in_separate_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "child.pid"
            program = (
                "import subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(100)'],start_new_session=True); "
                f"open({str(pidfile)!r},'w').write(str(p.pid)); time.sleep(100)"
            )

            class ChildChecker(Checker):
                def command(self, directory, theory):
                    return [sys.executable, "-c", program]

            result = ChildChecker({}).check(tmp, "Ignored", timeout=0.5)
            self.assertEqual(result["status"], "timeout")
            self.assertLess(result["elapsed_seconds"], 3)
            stat = Path("/proc") / pidfile.read_text() / "stat"
            if stat.exists():
                self.assertEqual(stat.read_text().rsplit(")", 1)[1].split()[0], "Z")
