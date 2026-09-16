import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vero.isabelle.benchmark import extract, prepare, read, render, save
from vero.isabelle.checker import Checker, evaluate
from vero.isabelle.planned_run import execute
from vero.isabelle.planning import compile_plan, dependencies


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        inputs = self.root / "input.jsonl"
        inputs.write_text(
            "\n".join(
                json.dumps(
                    {
                        "contract_id": str(i),
                        "name": f"t{i}",
                        "owner_theory": "Original",
                        "exact_statement": f'lemma t{i}: "True"',
                    }
                )
                for i in range(2)
            )
        )
        self.source = prepare(inputs, self.root / "benchmark", base_import="Main")
        self.plan = {
            "version": 1,
            "source_benchmark_id": self.source["benchmark_id"],
            "strategy": "provider then consumer",
            "modules": [
                {
                    "name": "Vero_Provider",
                    "contracts": ["c0001"],
                    "imports": [],
                    "rationale": "foundation",
                },
                {
                    "name": "Vero_Consumer",
                    "contracts": ["c0002"],
                    "imports": ["Vero_Provider"],
                    "rationale": "uses provider",
                },
            ],
            "execution_order": ["Vero_Provider", "Vero_Consumer"],
        }

    def test_coverage_cycles_and_reserved_names_rejected(self):
        for mutate in [
            lambda p: p["modules"][1]["contracts"].append("c0001"),
            lambda p: p["modules"][0]["imports"].append("Vero_Consumer"),
            lambda p: p["execution_order"].reverse(),
            lambda p: p["modules"][0].update(name="Vero_All"),
        ]:
            p = copy.deepcopy(self.plan)
            mutate(p)
            with self.assertRaises(ValueError):
                compile_plan(self.source, p)

    def test_reorganization_keeps_exact_specs_and_real_imports(self):
        bench = compile_plan(self.source, self.plan)
        self.assertEqual(
            [c["statement"] for c in bench["contracts"]],
            [c["statement"] for c in self.source["contracts"]],
        )
        self.assertEqual(dependencies(bench, "Vero_Consumer"), {"Vero_Provider"})
        render(bench, self.root / "render")
        self.assertIn(
            "imports Vero_Support Vero_Provider",
            (self.root / "render/Vero_Consumer.thy").read_text(),
        )

    def test_failed_provider_blocks_consumer_without_checking_it(self):
        bench = compile_plan(self.source, self.plan)
        render(
            bench,
            self.root / "render",
            {
                "c0001:prove": "by fail",
                "c0002:prove": "by (rule Vero_Provider.c0001_prove)",
            },
        )
        checker = Checker({})
        with patch.object(
            checker,
            "check",
            return_value={"status": "build_error", "audited": [], "log": "failed"},
        ) as check:
            report = evaluate(
                bench, extract(bench, self.root / "render"), self.root / "eval", checker
            )
        self.assertEqual(check.call_count, 1)
        self.assertEqual(report["contracts"]["c0002"]["status"], "blocked_dependency")

    def test_pipeline_consumes_accepted_plan_and_binds_resume(self):
        cfg = {
            "sandbox": str(self.root / "sandbox"),
            "agent": {"timeout_seconds": 10},
            "checker": {},
            "plan_seconds": 10,
            "total_agent_seconds": 10,
        }
        save(self.root / "config.json", cfg)

        def agent_run(**kwargs):
            save(Path(kwargs["sandbox_dir"]) / ".vero/plan.json", self.plan)
            return {"ok": True, "elapsed_seconds": 1}

        def generation(path, config, output, **kwargs):
            compiled = read(path)
            self.assertIn("planning", compiled)
            self.assertEqual(
                read(read(config)["agent"]["env"]["VERO_BENCHMARK"]), compiled
            )
            return {"status": "budget_exhausted"}

        with (
            patch("vero.isabelle.planned_run.CodexAgent.run", side_effect=agent_run),
            patch(
                "vero.isabelle.planned_run.Checker.check",
                return_value={"status": "ok", "log": "", "audited": []},
            ),
            patch("vero.isabelle.planned_run.run", side_effect=generation),
        ):
            result = execute(
                self.root / "benchmark/benchmark.json",
                self.root / "config.json",
                self.root / "pipeline",
            )
        self.assertEqual(result["planning"]["preflight"], "ok")
        cfg["plan_seconds"] = 11
        save(self.root / "config.json", cfg)
        with self.assertRaisesRegex(ValueError, "unchanged"):
            execute(
                self.root / "benchmark/benchmark.json",
                self.root / "config.json",
                self.root / "pipeline",
                resume=True,
            )

    def test_explicit_partial_plan_seed_retains_prior_budget(self):
        partial = copy.deepcopy(self.plan)
        partial["modules"] = partial["modules"][:1]
        partial["execution_order"] = partial["execution_order"][:1]
        cfg = {
            "sandbox": str(self.root / "sandbox"),
            "agent": {"timeout_seconds": 10},
            "checker": {},
            "plan_seconds": 10,
            "initial_plan": partial,
            "planning_continuation": {"previous_charged_plan_seconds": 600},
        }
        save(self.root / "config.json", cfg)

        def agent_run(**kwargs):
            path = Path(kwargs["sandbox_dir"]) / ".vero/plan.json"
            self.assertEqual(read(path), partial)
            save(path, self.plan)
            return {"ok": True, "elapsed_seconds": 1}

        with (
            patch("vero.isabelle.planned_run.CodexAgent.run", side_effect=agent_run),
            patch(
                "vero.isabelle.planned_run.Checker.check",
                return_value={"status": "ok", "log": "", "audited": []},
            ),
            patch(
                "vero.isabelle.planned_run.run",
                return_value={"status": "budget_exhausted"},
            ),
        ):
            result = execute(
                self.root / "benchmark/benchmark.json",
                self.root / "config.json",
                self.root / "pipeline",
            )
        self.assertEqual(
            result["planning_continuation"]["previous_charged_plan_seconds"], 600
        )

    @unittest.skipUnless(
        os.environ.get("VERO_TEST_ISABELLE"), "requires real Isabelle HOL"
    )
    def test_kernel_accepts_cross_theory_generated_fact(self):
        bench = compile_plan(self.source, self.plan)
        render(
            bench,
            self.root / "render",
            {
                "c0001:prove": "by simp",
                "c0002:prove": "by (rule Vero_Provider.c0001_prove)",
            },
        )
        checker = Checker(
            {
                "isabelle": os.environ["VERO_TEST_ISABELLE"],
                "session": "HOL",
                "timeout_seconds": 120,
            }
        )
        report = evaluate(
            bench, extract(bench, self.root / "render"), self.root / "eval", checker
        )
        self.assertTrue(report["complete"], report)
        self.assertEqual(report["proved_specs"], 2)
