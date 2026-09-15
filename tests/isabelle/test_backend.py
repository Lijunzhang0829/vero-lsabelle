import json
import tempfile
import unittest
from pathlib import Path

from vero.isabelle.benchmark import extract, prepare, render
from vero.isabelle.checker import evaluate, hygiene


class FakeChecker:
    def __init__(self):
        self.sources = []

    def check(self, directory, theory):
        text = (Path(directory) / (theory + ".thy")).read_text()
        self.sources.append(text)
        return {
            "status": "ok",
            "returncode": 0,
            "audited": ["c0001:prove"],
            "elapsed_seconds": 0,
            "log": "",
        }


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        data = self.root / "contracts.jsonl"
        data.write_text(
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
        self.bench = prepare(data, self.root / "bench", base_import="Main")
        self.source = self.root / "source"
        render(self.bench, self.source, {"c0001:prove": "by simp"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        a = extract(self.bench, self.source)
        self.assertEqual(a["slots"]["c0001:prove"]["body"], "by simp")
        self.assertFalse(a["file_errors"])

    def test_frozen_statement_tamper_not_replayed(self):
        f = self.source / "Vero_T.thy"
        f.write_text(f.read_text().replace('"True"', '"False"'))
        check = FakeChecker()
        r = evaluate(
            self.bench, extract(self.bench, self.source), self.root / "eval", check
        )
        self.assertEqual(r["proved_specs"], 1)
        self.assertIn('"True"', check.sources[0])
        self.assertNotIn('"False"', check.sources[0])

    def test_both_polarities_rejected(self):
        render(
            self.bench,
            self.source,
            {"c0001:prove": "by simp", "c0001:disprove": "by simp"},
        )
        r = evaluate(
            self.bench,
            extract(self.bench, self.source),
            self.root / "eval",
            FakeChecker(),
        )
        self.assertEqual(r["contracts"]["c0001"]["status"], "overfilled")

    def test_duplicate_marker_rejected(self):
        f = self.source / "Vero_T.thy"
        s = f.read_text()
        f.write_text(s + "\n" + s)
        self.assertIn("Vero_T", extract(self.bench, self.source)["file_errors"])

    def test_symlink_rejected(self):
        f = self.source / "Vero_T.thy"
        text = f.read_text()
        f.unlink()
        other = self.root / "escape"
        other.write_text(text)
        f.symlink_to(other)
        self.assertIn("Vero_T", extract(self.bench, self.source)["file_errors"])

    def test_malformed_and_unsafe_bodies(self):
        for body in [
            "by (tactic \\<open>all_tac\\<close>)",
            "sorry",
            "by simp\nend\naxiomatization bad: False",
            "by simp (* unfinished",
        ]:
            self.assertIsNotNone(hygiene(body))

    def test_unfilled_is_not_proved(self):
        render(self.bench, self.source)
        r = evaluate(
            self.bench,
            extract(self.bench, self.source),
            self.root / "eval",
            FakeChecker(),
        )
        self.assertEqual(r["proved_specs"], 0)
        self.assertFalse(r["complete"])

    def test_missing_audit_is_not_proved(self):
        class NoAudit(FakeChecker):
            def check(self, *args):
                return {"status": "ok", "returncode": 0, "audited": [], "log": ""}

        r = evaluate(
            self.bench, extract(self.bench, self.source), self.root / "eval", NoAudit()
        )
        self.assertEqual(r["contracts"]["c0001"]["status"], "missing_theorem")

    def test_fresh_evaluation_required(self):
        out = self.root / "eval"
        out.mkdir()
        with self.assertRaises(FileExistsError):
            evaluate(self.bench, extract(self.bench, self.source), out, FakeChecker())

    def test_bound_artifact(self):
        a = extract(self.bench, self.source)
        a["benchmark_id"] = "wrong"
        with self.assertRaises(ValueError):
            evaluate(self.bench, a, self.root / "eval", FakeChecker())


if __name__ == "__main__":
    unittest.main()
