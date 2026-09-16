import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vero.isabelle.attempts import begin
from vero.isabelle.benchmark import digest, extract, prepare, read, render, save
from vero.isabelle.checker import Checker, evaluate
from vero.isabelle.runner import run
from vero.isabelle.slots import auxiliary_hygiene, context, schedule, upgrade


class SlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        inputs = self.root / "contracts.jsonl"
        inputs.write_text(
            "\n".join(
                json.dumps(
                    {
                        "contract_id": str(i),
                        "name": f"t{i}",
                        "owner_theory": m,
                        "exact_statement": f'lemma t{i}: "True"',
                    }
                )
                for i, m in enumerate(["Provider", "Consumer"])
            )
        )
        self.bench = prepare(inputs, self.root / "bench", base_import="Main")
        self.bodies = {
            "Vero_Provider:global_aux": 'lemma vero_aux_shared: "True" by simp',
            "c0001:prove_aux": 'lemma vero_aux_local: "True" by (rule vero_aux_shared)',
            "c0001:prove": "by (rule vero_aux_local)",
            "Vero_Consumer:imports": "Vero_Provider",
            "c0002:prove": "by (rule Vero_Provider.vero_aux_shared)",
        }

    def test_auxiliary_roundtrip_and_fresh_replay(self):
        render(self.bench, self.root / "source", self.bodies)
        a = extract(self.bench, self.root / "source")
        self.assertFalse(a["file_errors"])
        for k, v in self.bodies.items():
            self.assertEqual(a["slots"][k]["body"], v)
        f = self.root / "source/Vero_Provider.thy"
        f.write_text(f.read_text() + '\nlemma outside: "False" by simp\n')
        a = extract(self.bench, self.root / "source")
        render(
            self.bench,
            self.root / "replay",
            {k: v["body"] for k, v in a["slots"].items() if v["found"]},
        )
        text = (self.root / "replay/Vero_Provider.thy").read_text()
        self.assertNotIn("outside", text)
        self.assertIn("lemma vero_aux_shared", text)

    def test_import_topology_and_rejection(self):
        c = context(self.bench, self.bodies)
        self.assertEqual(
            c["planning"]["execution_order"], ["Vero_Provider", "Vero_Consumer"]
        )
        for bad in ["../Original", "Vero_Consumer", "Vero_Unknown"]:
            with self.assertRaises(ValueError):
                context(self.bench, {**self.bodies, "Vero_Consumer:imports": bad})
        with self.assertRaisesRegex(ValueError, "cycle"):
            context(
                self.bench, {**self.bodies, "Vero_Provider:imports": "Vero_Consumer"}
            )

    def test_legacy_identity_and_slot_schedule_preserved(self):
        old = copy.deepcopy(self.bench)
        old["schema_version"] = 1
        old.pop("benchmark_id")
        old["benchmark_id"] = digest(old)
        render(old, self.root / "old", {"c0001:prove": "by simp"})
        self.assertEqual(
            set(extract(old, self.root / "old")["slots"]),
            {"c0001:prove", "c0001:disprove", "c0002:prove", "c0002:disprove"},
        )
        upgraded = upgrade(old)
        self.assertEqual(upgraded["contracts"], old["contracts"])
        self.assertNotEqual(upgraded["benchmark_id"], old["benchmark_id"])
        self.assertIn("Vero_Provider:global_aux", schedule(upgraded))

    def test_bad_helper_commands_and_shadowing_rejected(self):
        for text in [
            'lemma True: "False" by simp',
            'lemma vero_aux_bad: "False" sorry',
            'lemma vero_aux_bad: "True" oops',
            'definition state_relation where "state_relation = {}"',
            'lemma vero_aux_t: "True" by simp\nend\naxiomatization x :: bool',
            'ML \\<open>writeln "bad"\\<close>',
            'lemma vero_aux_t: "True" by simp\nhide_const True',
        ]:
            self.assertIsNotNone(auxiliary_hygiene(text), text)
        self.assertIsNone(
            auxiliary_hygiene(
                'definition vero_aux_id where "vero_aux_id (x::nat) = x"\nlemma vero_aux_id_eq: "vero_aux_id x = x" by (simp add: vero_aux_id_def)'
            )
        )

    def test_attempt_preserves_complete_dependency_artifact(self):
        render(self.bench, self.root / "source", self.bodies)
        a = extract(self.bench, self.root / "source")
        p = begin(self.root / "attempts", self.bench, "Vero_Consumer", a)
        saved = read(p / "submitted.json")
        self.assertEqual(saved["context_artifact"], a)
        self.assertEqual(set(saved["slots"]), {"c0002:prove", "c0002:disprove"})

    def test_auxiliary_only_provider_failure_blocks_consumer(self):
        bodies = {
            **self.bodies,
            "c0001:prove": "oops",
            "c0001:prove_aux": "",
            "Vero_Provider:global_aux": 'lemma vero_aux_shared: "False" by simp',
        }
        render(self.bench, self.root / "source", bodies)
        checker = Checker({})
        with patch.object(
            checker,
            "check",
            return_value={"status": "build_error", "log": "bad helper", "audited": []},
        ) as call:
            r = evaluate(
                self.bench,
                extract(self.bench, self.root / "source"),
                self.root / "eval",
                checker,
            )
        self.assertEqual(call.call_count, 1)
        self.assertEqual(r["contracts"]["c0002"]["status"], "blocked_dependency")

    def test_cycle_never_reaches_native_checker(self):
        render(
            self.bench,
            self.root / "source",
            {**self.bodies, "Vero_Provider:imports": "Vero_Consumer"},
        )
        with patch.object(
            Checker, "check", side_effect=AssertionError("cycle compiled")
        ):
            r = evaluate(
                self.bench,
                extract(self.bench, self.root / "source"),
                self.root / "eval",
                Checker({}),
            )
        self.assertIn("cycle", r["context_error"])
        self.assertEqual(r["proved_specs"], 0)

    def test_iteration_seed_preserves_helpers_and_imports(self):
        cfg = {
            "sandbox": str(self.root / "source"),
            "max_iterations": 2,
            "total_agent_seconds": 10,
            "resume_artifact": True,
            "agent": {"timeout_seconds": 5},
            "checker": {},
        }
        save(self.root / "config.json", cfg)
        calls = []

        def agent(**kw):
            if calls:
                a = extract(self.bench, kw["sandbox_dir"])
                for k, v in self.bodies.items():
                    self.assertEqual(a["slots"][k]["body"], v)
                self.assertTrue((Path(kw["sandbox_dir"]) / "FEEDBACK.md").is_file())
            else:
                render(self.bench, kw["sandbox_dir"], self.bodies)
            calls.append(1)
            return {"ok": True, "elapsed_seconds": 1}

        def grader(b, a, d, c):
            d.mkdir()
            (d / "report.md").write_text("continue")
            return {
                "benchmark_id": b["benchmark_id"],
                "proved_specs": 0,
                "disproved_specs": 0,
                "complete": False,
                "contracts": {x["id"]: {"status": "unfilled"} for x in b["contracts"]},
            }

        with (
            patch("vero.isabelle.runner.CodexAgent.run", side_effect=agent),
            patch("vero.isabelle.runner.evaluate", side_effect=grader),
        ):
            run(
                self.root / "bench/benchmark.json",
                self.root / "config.json",
                self.root / "run",
            )
        self.assertEqual(len(calls), 2)

    @unittest.skipUnless(
        os.environ.get("VERO_TEST_ISABELLE"), "requires native Isabelle"
    )
    def test_kernel_shared_helpers_definitions_and_dynamic_imports(self):
        bodies = {
            **self.bodies,
            "Vero_Provider:global_aux": 'definition vero_aux_id where "vero_aux_id (x::nat) = x"\nlemma vero_aux_shared: "True" by simp',
        }
        render(self.bench, self.root / "source", bodies)
        c = Checker(
            {
                "isabelle": os.environ["VERO_TEST_ISABELLE"],
                "session": "HOL",
                "timeout_seconds": 90,
            }
        )
        r = evaluate(
            self.bench, extract(self.bench, self.root / "source"), self.root / "eval", c
        )
        self.assertTrue(r["complete"], r)
        self.assertEqual(r["proved_specs"], 2)

    @unittest.skipUnless(
        os.environ.get("VERO_TEST_ISABELLE"), "requires native Isabelle"
    )
    def test_kernel_helper_sorry_cannot_prove_target_even_bypassing_hygiene(self):
        render(
            self.bench,
            self.root / "source",
            {
                **self.bodies,
                "Vero_Provider:global_aux": 'lemma vero_aux_shared: "True" sorry',
            },
        )

        class Dirty(Checker):
            def command(self, *args):
                cmd = super().command(*args)
                cmd[2:2] = ["-o", "quick_and_dirty=true"]
                return cmd

        r = Dirty(
            {"isabelle": os.environ["VERO_TEST_ISABELLE"], "session": "HOL"}
        ).check(self.root / "source", "Vero_Provider")
        self.assertEqual(r["status"], "build_error")
        self.assertNotIn("c0001:prove", r["audited"])
        self.assertIn("skip_proof", r["log"])


@unittest.skipUnless(os.environ.get("VERO_TEST_ISABELLE"), "requires native Isabelle")
class AuxiliaryAxiomTests(unittest.TestCase):
    def test_arbitrary_axiom_with_reserved_name_is_not_a_definition(self):
        fixture = SlotTests()
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        render(
            fixture.bench,
            fixture.root / "source",
            {
                "Vero_Provider:global_aux": 'axiomatization vero_aux_bad :: bool where vero_aux_false: "False"',
                "c0001:prove": "by simp",
            },
        )
        checker = Checker(
            {"isabelle": os.environ["VERO_TEST_ISABELLE"], "session": "HOL"}
        )
        result = checker.check(fixture.root / "source", "Vero_Provider")
        self.assertEqual(result["status"], "build_error")
        self.assertIn("new axioms", result["log"])
        self.assertNotIn("c0001:prove", result["audited"])


class AuxiliaryScopeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("VERO_TEST_ISABELLE"), "requires native Isabelle"
    )
    def test_proof_aux_keeps_fixed_context_and_is_shared_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / "contracts.jsonl"
            inputs.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "contract_id": str(i),
                            "name": f"t{i}",
                            "owner_theory": "Context",
                            "exact_statement": f'lemma t{i}: "x = x"',
                            "contract_owned_context": ["context fixes x :: nat begin"],
                        }
                    )
                    for i in range(2)
                )
            )
            b = prepare(inputs, root / "bench", base_import="Main")
            render(
                b,
                root / "source",
                {
                    "c0001:prove_aux": 'lemma vero_aux_x: "x = x" by simp',
                    "c0001:prove": "by (rule vero_aux_x)",
                    "c0002:prove": "by (rule vero_aux_x)",
                },
            )
            c = Checker(
                {"isabelle": os.environ["VERO_TEST_ISABELLE"], "session": "HOL"}
            )
            r = evaluate(b, extract(b, root / "source"), root / "eval", c)
            self.assertTrue(r["complete"], r)

    def test_helper_error_location_is_distinct_from_contract(self):
        from vero.isabelle.attempts import finish

        fixture = SlotTests()
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        render(fixture.bench, fixture.root / "source", fixture.bodies)
        a = extract(fixture.bench, fixture.root / "source")
        p = begin(fixture.root / "attempts", fixture.bench, "Vero_Provider", a)
        f = fixture.root / "source/Vero_Provider.thy"
        line = next(
            i
            for i, s in enumerate(f.read_text().splitlines(), 1)
            if s.startswith("lemma vero_aux_local")
        )
        finish(
            p,
            {
                "status": "build_error",
                "audited": [],
                "log": f'error (line {line} of "{f}")',
            },
            f,
        )
        r = read(p / "result.json")
        self.assertEqual(r["diagnostic_auxiliary_slot"], "c0001:prove_aux")
        self.assertIsNone(r["diagnostic_slot"])
