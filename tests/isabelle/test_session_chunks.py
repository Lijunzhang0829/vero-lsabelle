import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vero.isabelle.agent import CodexAgent
from vero.isabelle.benchmark import extract, prepare, read, render, save
from vero.isabelle.runner import next_position, run


class ChunkTests(unittest.TestCase):
    def test_two_axis_position_and_charged_interruption(self):
        cfg = {
            "max_iterations": 2,
            "iteration_time_chunk_seconds": 10,
            "iteration_max_time_seconds": 20,
        }
        j = {"iterations": []}
        self.assertEqual(next_position(j, cfg), (0, 0))
        j["iterations"].append(
            {
                "iteration": 0,
                "chunk": 0,
                "agent": {"elapsed_seconds": 10, "recovered_interruption": True},
            }
        )
        self.assertEqual(next_position(j, cfg), (0, 1))
        j["iterations"].append(
            {
                "iteration": 0,
                "chunk": 1,
                "agent": {"elapsed_seconds": 10, "timed_out": True},
            }
        )
        self.assertEqual(next_position(j, cfg), (1, 0))
        j["iterations"].append(
            {"iteration": 1, "chunk": 0, "agent": {"elapsed_seconds": 1, "ok": True}}
        )
        self.assertIsNone(next_position(j, cfg))

    def test_persistent_resume_command_uses_installed_cli_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "fake-codex"
            binary.write_text(
                '#!/usr/bin/env python3\nprint(\'{"type":"turn.completed","usage":{}}\')\n'
            )
            binary.chmod(0o755)
            prompt = root / "instruction"
            prompt.write_text("test")
            cfg = {
                "codex": str(binary),
                "home": str(root / "home"),
                "model": "fixture",
                "timeout_seconds": 5,
                "persistent_session": True,
                "resume_session": True,
            }
            r = CodexAgent(cfg, root / "log").run(
                sandbox_dir=root, instruction_file=prompt
            )
            self.assertTrue(r["ok"])
            cmd = read(root / "log/invocation.json")["command"]
            self.assertEqual(cmd[1:4], ["exec", "resume", "--last"])
            self.assertNotIn("--ephemeral", cmd)
            self.assertNotIn("--add-dir", cmd)
            self.assertNotIn("--sandbox", cmd)

    def test_checkpoint_keeps_workspace_helpers_and_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / "input.jsonl"
            inputs.write_text(
                json.dumps(
                    {
                        "contract_id": "x",
                        "name": "x",
                        "owner_theory": "T",
                        "exact_statement": 'lemma x: "True"',
                    }
                )
                + "\n"
            )
            bench = prepare(inputs, root / "bench", base_import="Main")
            cfg = {
                "sandbox": str(root / "source"),
                "max_iterations": 1,
                "total_agent_seconds": 6,
                "iteration_time_chunk_seconds": 3,
                "iteration_max_time_seconds": 6,
                "agent": {"timeout_seconds": 3},
                "checker": {},
            }
            save(root / "config.json", cfg)
            seen = []

            def agent(obj, **kwargs):
                workspace = Path(kwargs["sandbox_dir"])
                if not seen:
                    render(
                        bench,
                        workspace,
                        {
                            "Vero_T:global_aux": 'lemma vero_aux_t: "True" by simp',
                            "c0001:prove": "by (rule vero_aux_t)",
                        },
                    )
                    (workspace / "session-note").write_text("persistent")
                else:
                    self.assertTrue(obj.config["resume_session"])
                    self.assertTrue((workspace / "session-note").exists())
                    self.assertIn(
                        "vero_aux_t",
                        extract(bench, workspace)["slots"]["Vero_T:global_aux"]["body"],
                    )
                    self.assertTrue((workspace / "FEEDBACK.md").exists())
                seen.append(obj.config)
                return {"ok": False, "timed_out": True, "elapsed_seconds": 3}

            def grader(b, a, d, c):
                d.mkdir()
                (d / "report.md").write_text("feedback")
                return {
                    "benchmark_id": b["benchmark_id"],
                    "proved_specs": 0,
                    "disproved_specs": 0,
                    "complete": False,
                    "contracts": {"c0001": {"status": "unfilled"}},
                }

            with (
                patch.object(CodexAgent, "run", agent),
                patch("vero.isabelle.runner.evaluate", side_effect=grader),
            ):
                j = run(
                    root / "bench/benchmark.json", root / "config.json", root / "run"
                )
            self.assertEqual(len(seen), 2)
            self.assertFalse(seen[0]["resume_session"])
            self.assertEqual(j["elapsed_agent_seconds"], 6)
            self.assertEqual(
                [(r["iteration"], r["chunk"]) for r in j["iterations"]],
                [(0, 0), (0, 1)],
            )
