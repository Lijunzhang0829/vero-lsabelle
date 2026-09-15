import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vero.isabelle.attempts import archive, begin, finish, ledger, summarize
from vero.isabelle.benchmark import extract, prepare, render


class AttemptTests(unittest.TestCase):
    def fixture(self, root):
        contracts = root / "contracts.jsonl"
        contracts.write_text(
            "\n".join(
                json.dumps(
                    {
                        "contract_id": str(i),
                        "name": f"t{i}",
                        "owner_theory": "T",
                        "exact_statement": f'lemma t{i}: "True"',
                    }
                )
                for i in range(2)
            )
        )
        bench = prepare(contracts, root / "bench", base_import="Main")
        render(
            bench, root / "source", {"c0001:prove": "by simp", "c0002:prove": "by fail"}
        )
        return bench, extract(bench, root / "source")

    def test_failing_line_does_not_blame_audited_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bench, artifact = self.fixture(root)
            module = bench["contracts"][0]["module"]
            path = begin(root / "attempts", bench, module, artifact)
            source = root / "source" / f"{module}.thy"
            line = next(
                i
                for i, l in enumerate(source.read_text().splitlines(), 1)
                if l == "by fail"
            )
            finish(
                path,
                {
                    "status": "build_error",
                    "audited": ["c0001:prove"],
                    "log": f'*** Failed to finish proof (line {line} of "{source}")',
                },
                source,
            )
            result = json.loads((path / "result.json").read_text())
            self.assertEqual(result["diagnostic_slot"], "c0002:prove")
            self.assertEqual(
                result["outcomes"]["c0002:prove"], "diagnostic_at_this_contract"
            )
            self.assertEqual(
                result["outcomes"]["c0001:prove"],
                "module_check_incomplete_unattributed",
            )
            self.assertTrue((path / "source.thy").exists())

    def test_interrupted_and_final_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bench, artifact = self.fixture(root)
            begin(root / "attempts", bench, bench["contracts"][0]["module"], artifact)
            evaluation = {
                "benchmark_id": bench["benchmark_id"],
                "contracts": {
                    "c0001": {"status": "proved"},
                    "c0002": {"status": "unfilled"},
                },
            }
            result = ledger(bench, evaluation, attempt_roots=[root / "attempts"])
            self.assertEqual(
                result["contracts"][0]["current_state"], "independently_proved"
            )
            self.assertEqual(
                result["contracts"][1]["attempts"][0]["outcome"], "submitted_no_result"
            )

    def test_legacy_module_failure_does_not_invent_contract_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bench, _ = self.fixture(root)
            trace = root / "trace.jsonl"
            trace.write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": "python3 /backend/tool.py check "
                            + bench["contracts"][0]["module"],
                            "exit_code": 1,
                            "aggregated_output": "failed",
                        },
                    }
                )
                + "\n"
            )
            e = {
                "benchmark_id": bench["benchmark_id"],
                "contracts": {
                    c["id"]: {"status": "unfilled"} for c in bench["contracts"]
                },
            }
            result = ledger(bench, e, traces=[trace])
            self.assertTrue(
                all(
                    r["current_state"] == "unresolved_attempt_history_unknown"
                    for r in result["contracts"]
                )
            )

    def test_archive_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = root / ".vero-attempts/a"
            record.mkdir(parents=True)
            secret = root / "other"
            secret.write_text("not an attempt")
            (record / "submitted.json").symlink_to(secret)
            archive(root, root / "archive")
            self.assertFalse((root / "archive/a/submitted.json").exists())

    def test_tool_records_real_invocation_and_survives_tmp_cleanup(self):
        from vero.isabelle import tool
        from vero.isabelle.benchmark import read

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bench, _ = self.fixture(root)
            module = bench["contracts"][0]["module"]
            previous = Path.cwd()
            try:
                os.chdir(root / "source")
                with (
                    patch.dict(
                        os.environ,
                        {"VERO_BENCHMARK": str(root / "bench/benchmark.json")},
                    ),
                    patch("sys.argv", ["tool", "check", module]),
                    patch.object(
                        tool,
                        "read",
                        side_effect=lambda p: (
                            {} if p == "/benchmark/checker.json" else read(p)
                        ),
                    ),
                    patch.object(
                        tool.Checker,
                        "check",
                        return_value={
                            "status": "timeout",
                            "audited": [],
                            "log": "timeout",
                            "elapsed_seconds": 120,
                        },
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                    self.assertRaises(SystemExit) as exited,
                ):
                    tool.main()
                self.assertEqual(exited.exception.code, 1)
            finally:
                os.chdir(previous)
            records = list((root / "source/.vero-attempts").glob("*/result.json"))
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0].with_name("source.thy").exists())
            self.assertEqual(json.loads(records[0].read_text())["status"], "timeout")

    def test_corrupt_telemetry_does_not_override_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bench, _ = self.fixture(root)
            folder = root / "attempts/broken"
            folder.mkdir(parents=True)
            (folder / "submitted.json").write_text("{")
            evaluation = {
                "benchmark_id": bench["benchmark_id"],
                "contracts": {
                    c["id"]: {"status": "unfilled"} for c in bench["contracts"]
                },
            }
            result = summarize(bench, evaluation, root / "attempts")
            self.assertIn("telemetry_error", result)
            self.assertEqual(len(result["contracts"]), 2)
