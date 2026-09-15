"""Opt-in real Isabelle tests: VERO_TEST_ISABELLE=/path/to/isabelle."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from vero.isabelle.benchmark import extract, prepare, render
from vero.isabelle.checker import Checker, evaluate


@unittest.skipUnless(
    os.environ.get("VERO_TEST_ISABELLE"), "requires Isabelle with HOL heap"
)
class KernelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        fixture = (
            Path(__file__).resolve().parents[2]
            / "reference/IsabelleMini/contracts.jsonl"
        )
        self.bench = prepare(fixture, self.root / "benchmark", base_import="Main")
        self.checker = Checker(
            {
                "isabelle": os.environ["VERO_TEST_ISABELLE"],
                "session": "HOL",
                "timeout_seconds": 90,
            }
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_positive_counterexample_and_vacuous_contract(self):
        source = self.root / "source"
        render(
            self.bench,
            source,
            {
                "c0001:prove": "by simp",
                "c0002:disprove": "by simp",
                "c0003:prove": "using impossible by assumption",
                "c0004:disprove": "by auto",
            },
        )
        report = evaluate(
            self.bench, extract(self.bench, source), self.root / "eval", self.checker
        )
        self.assertEqual(report["proved_specs"], 2)
        self.assertEqual(report["disproved_specs"], 2)
        self.assertEqual(report["integration"]["status"], "ok")
        self.assertFalse(report["complete"])

    def test_false_assumption_cannot_refute_vacuous_contract(self):
        source = self.root / "source"
        render(self.bench, source, {"c0003:disprove": "by auto"})
        result = self.checker.check(source, "Vero_Mini")
        self.assertEqual(result["status"], "build_error")
        self.assertNotIn("c0003:disprove", result["audited"])

    def test_quantified_assumptions_keep_binders_and_distinct_types(self):
        fixture = self.root / "quantified.jsonl"
        fixture.write_text(
            json.dumps(
                {
                    "contract_id": "q",
                    "name": "q",
                    "owner_theory": "Quantified",
                    "exact_statement": 'lemma q: fixes P :: "bool => bool" assumes a: "!!s::bool. P s" and b: "!!s::nat. s = s" shows "False"',
                }
            )
            + "\n"
        )
        bench = prepare(fixture, self.root / "quantified", base_import="Main")
        source = self.root / "source"
        render(bench, source, {"c0001:disprove": "by auto"})
        result = self.checker.check(source, "Vero_Quantified")
        self.assertEqual(result["status"], "ok", result["log"])
        self.assertIn("c0001:disprove", result["audited"])

    def test_kernel_rejects_sorry_even_without_lexical_guard(self):
        source = self.root / "source"
        render(self.bench, source, {"c0002:prove": "sorry"})

        class DirtyChecker(Checker):
            def command(self, directory, theory):
                command = super().command(directory, theory)
                command[2:2] = ["-o", "quick_and_dirty=true"]
                return command

        result = DirtyChecker(self.checker.config).check(source, "Vero_Mini")
        self.assertEqual(result["status"], "build_error")
        self.assertIn("skip_proof", result["log"])
        self.assertNotIn("c0002:prove", result["audited"])
