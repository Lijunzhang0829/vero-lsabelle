"""Resource-v2 accounting over immutable events, without causal cost guesses.

Scope is explicit metadata, never inferred from a nearby prover check. Recovered
usage retains its original response timestamp. ``cutoff`` reconstructs a frozen
stage boundary while the same run may continue accumulating later events.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .metrics import _activity, _cost_quality
from .telemetry import EventLog


def _ids(row):
    value = row.get("target_ids")
    if value is None and ("contract_ids" in row or "helper_ids" in row):
        value = row.get("contract_ids", []) + row.get("helper_ids", [])
    if value is not None:
        return list(dict.fromkeys(value))
    value = row.get("target_id", row.get("contract_id", row.get("node_id")))
    return [value] if value else []


def _scope(row):
    return {
        key: row.get(key)
        for key in (
            "target_ids",
            "theory_ids",
            "phase",
            "attribution_basis",
            "confidence",
            "scope_kind",
        )
    }


def export_resource_metrics(
    log,
    *,
    targets=(),
    as_of=None,
    cutoff=None,
    token_thresholds=(),
    milestones=(10, 30, 50, 75, 100),
):
    """Export explicit scopes and three coverage levels; never mutate a run.

    ``log`` is EventLog, its directory, or an iterable of event dictionaries.
    ``targets`` supplies the complete fixed inventory, including never attempted
    contracts, as dictionaries with target_id, target_kind, theory_id, version.
    Supported v2 event payloads are described in the module's unit tests.
    """
    if isinstance(log, (str, Path)):
        log = EventLog.open(log)
    manifest = log.manifest if isinstance(log, EventLog) else {}
    source = log.events() if isinstance(log, EventLog) else list(log)
    end = time.time() if as_of is None else float(as_of)
    if cutoff is not None:
        end = min(end, float(cutoff))
    events = sorted(
        (r for r in source if r["timestamp"] <= end),
        key=lambda r: (r["timestamp"], r.get("recorded_at", 0), r.get("event_id", "")),
    )
    run_ids = {r["run_id"] for r in events if r.get("run_id")}
    if len(run_ids) > 1:
        raise ValueError("cannot combine independent run identities")
    unique, scopes = {}, {}
    for row in events:
        if row["kind"] == "response_scope":
            key = row["response_id"]
            value = _scope(row)
            if key in scopes and scopes[key] != value:
                raise ValueError("conflicting response scope")
            scopes[key] = value
        if row["kind"] != "response_usage":
            continue
        key = row.get("response_id")
        if not key:
            raise ValueError("response identity missing")
        if key in unique:
            if any(
                unique[key].get(k) != row.get(k)
                for k in ("usage", "timestamp", "model", "thread_id")
            ):
                raise ValueError("conflicting duplicate response")
        else:
            unique[key] = dict(row)
    responses = list(unique.values())
    targets = list(targets)
    inventory = {}
    checks_seen = {}

    def register(row):
        for ident in _ids(row):
            target = inventory.setdefault(
                ident,
                {
                    "target_id": ident,
                    "target_kind": "unknown",
                    "theory_id": None,
                    "version": None,
                    "status": "pending",
                    "first_green": None,
                    "current_seal": None,
                    "integrated": None,
                    "first_seal": None,
                    "first_integrated": None,
                    "direct_response_ids": [],
                    "shared_response_ids": [],
                    "helper_consumers": [],
                    "check_count": None,
                    "failed_check_count": None,
                    "observed_check_seconds": None,
                    "check_count_basis": "explicit_target_association_lower_bound",
                    "related_theory_check_ids": [],
                    "candidate_count": 0,
                    "failed_candidate_count": 0,
                    "last_candidate_status": None,
                    "candidate_history": [],
                    "helper_relations": [],
                    "plan_version": None,
                    "green_source_version": None,
                    "registration_versions": [],
                    "missing_check_durations": 0,
                },
            )
            for key in ("target_kind", "theory_id", "version", "plan_version"):
                if row.get(key) is not None:
                    target[key] = row[key]
        return _ids(row)

    for ident in manifest.get("contract_ids", []):
        register({"target_id": ident, "target_kind": "contract"})
    for row in targets:
        register(row)
    origin = min(
        (
            r["timestamp"]
            for r in events
            if r["kind"] == "lifecycle" and r.get("action") == "start"
        ),
        default=min((r["timestamp"] for r in events), default=end),
    )
    first, green, sealed, integrated = set(), set(), set(), set()
    coverage, segments = [], []

    def snapshot(when):
        contracts = {k for k, v in inventory.items() if v["target_kind"] == "contract"}
        return {
            "timestamp": when,
            "wall_seconds": max(0, when - origin),
            "first_green_contracts": len(first & contracts),
            "current_green_contracts": len(green & contracts),
            "current_sealed_contracts": len(sealed & contracts),
            "integrated_contracts": len(integrated & contracts),
            **_activity(events, when),
            **_cost_quality(
                [r for r in responses if r["timestamp"] <= when], events, when
            ),
        }

    # A zero baseline is justified only by an explicit fresh start and supplied
    # complete inventory. An excerpt/continuation without that evidence is unknown.
    if (
        (manifest.get("contract_ids") or targets)
        and any(
            r["kind"] == "lifecycle"
            and r.get("action") == "start"
            and r["timestamp"] == origin
            for r in events
        )
        and not any(r["timestamp"] < origin for r in events)
    ):
        coverage.append(
            {**snapshot(origin), "kind": "initial_inventory", "event_id": None}
        )

    for row in events:
        kind, ids = row["kind"], _ids(row)
        if kind in {
            "target_registered",
            "contract_accepted",
            "helper_accepted",
            "target_green",
        }:
            payload = dict(row)
            if kind in {"contract_accepted", "helper_accepted"}:
                payload["target_kind"] = kind.removesuffix("_accepted")
            register(payload)
            if kind == "target_registered":
                for ident in ids:
                    inventory[ident]["registration_versions"].append(
                        {
                            "timestamp": row["timestamp"],
                            "version": row.get("version"),
                            "plan_version": row.get("plan_version"),
                        }
                    )
        if kind in {"contract_accepted", "helper_accepted", "target_green"}:
            for ident in ids:
                first.add(ident)
                green.add(ident)
                inventory[ident]["status"] = "green"
                inventory[ident]["green_source_version"] = row.get("version")
                inventory[ident]["first_green"] = inventory[ident]["first_green"] or {
                    **snapshot(row["timestamp"]),
                    "evidence": row.get("evidence", row.get("evidence_id")),
                }
        elif kind in {
            "targets_invalidated",
            "target_invalidated",
            "theory_invalidated",
        }:
            if not ids and row.get("theory_id"):
                ids = [
                    k
                    for k, v in inventory.items()
                    if v["theory_id"] == row["theory_id"]
                ]
            for ident in ids:
                green.discard(ident)
                sealed.discard(ident)
                integrated.discard(ident)
                if ident in inventory:
                    inventory[ident].update(
                        status="invalidated", current_seal=None, integrated=None
                    )
        elif kind == "theory_sealed":
            for ident in ids:
                if ident not in green:
                    raise ValueError(
                        "seal includes a target without current GREEN: " + ident
                    )
                sealed.add(ident)
                inventory[ident]["current_seal"] = {
                    **snapshot(row["timestamp"]),
                    "theory_id": row.get("theory_id"),
                    "version": row.get("version"),
                    "evidence": row.get("evidence", row.get("evidence_id")),
                }
                inventory[ident]["first_seal"] = (
                    inventory[ident]["first_seal"] or inventory[ident]["current_seal"]
                )
                inventory[ident]["status"] = "sealed"
        elif kind == "repository_integrated":
            for ident in ids:
                if ident not in sealed:
                    raise ValueError(
                        "integration includes an unsealed target: " + ident
                    )
                integrated.add(ident)
                inventory[ident]["integrated"] = snapshot(row["timestamp"])
                inventory[ident]["first_integrated"] = (
                    inventory[ident]["first_integrated"]
                    or inventory[ident]["integrated"]
                )
                inventory[ident]["status"] = "integrated"
        elif kind == "helper_relation":
            register({"target_id": row["helper_id"], "target_kind": "helper"})
            inventory[row["helper_id"]]["helper_consumers"] = sorted(
                set(
                    inventory[row["helper_id"]]["helper_consumers"]
                    + row.get("consumer_ids", [])
                )
            )
            inventory[row["helper_id"]]["helper_relations"].append(
                {
                    "timestamp": row["timestamp"],
                    "consumer_ids": row.get("consumer_ids", []),
                    "dependency_ids": row.get("dependency_ids", []),
                    "relation_basis": row.get(
                        "relation_basis", "unspecified_declaration"
                    ),
                    "plan_version": row.get("plan_version"),
                }
            )
        elif kind == "candidate_finished":
            for ident in ids:
                register({"target_id": ident})
                item = inventory[ident]
                item["candidate_count"] += 1
                item["failed_candidate_count"] += row.get("status") != "proved"
                item["last_candidate_status"] = row.get("status")
                item["candidate_history"].append(
                    {
                        k: row.get(k)
                        for k in (
                            "timestamp",
                            "status",
                            "attempt",
                            "evidence_id",
                            "plan_version",
                        )
                    }
                )
        elif kind == "check_finished":
            check_id = row.get(
                "check_id", row.get("event_id", "observation:" + str(len(checks_seen)))
            )
            if check_id in checks_seen:
                if checks_seen[check_id] != row:
                    raise ValueError("conflicting duplicate native check")
                continue
            checks_seen[check_id] = row
            theories = row.get("target", [])
            if isinstance(theories, str):
                theories = [theories]
            for ident, item in inventory.items():
                if not ids and item["theory_id"] in (theories or []):
                    item["related_theory_check_ids"].append(check_id)
            for ident in ids:
                register({"target_id": ident})
                item = inventory[ident]
                item["check_count"] = (item["check_count"] or 0) + 1
                item["failed_check_count"] = (item["failed_check_count"] or 0) + (
                    row.get("status")
                    not in {
                        "success",
                        "green",
                        "passed",
                        "ok",
                        "proved",
                    }
                )
                duration = row.get("duration_seconds", row.get("elapsed_seconds"))
                if duration is None:
                    item["missing_check_durations"] += 1
                else:
                    item["observed_check_seconds"] = (
                        item["observed_check_seconds"] or 0
                    ) + duration
        if kind in {
            "contract_accepted",
            "helper_accepted",
            "target_green",
            "targets_invalidated",
            "target_invalidated",
            "theory_invalidated",
            "theory_sealed",
            "repository_integrated",
        }:
            coverage.append(
                {
                    **snapshot(row["timestamp"]),
                    "event_id": row.get("event_id"),
                    "kind": kind,
                }
            )
        if kind in {"stage_cutoff", "resource_stage_ended"}:
            segments.append({**row, "metrics": snapshot(row["timestamp"])})

    pools = {k: [] for k in ("direct", "shared", "unattributed")}
    for row in responses:
        scope = scopes.get(row["response_id"], _scope(row))
        row.update(scope)
        ids = list(dict.fromkeys(scope.get("target_ids") or []))
        # Declared labels require a stated basis; otherwise retain them only as metadata.
        pool = "direct" if len(ids) == 1 else "shared" if ids else "unattributed"
        if scope.get("scope_kind") == "repository" and scope.get("attribution_basis"):
            pool = "shared"
        if not scope.get("attribution_basis"):
            pool = "unattributed"
        row["cost_pool"] = pool
        pools[pool].append(row)
        if pool != "unattributed":
            for ident in ids:
                register({"target_id": ident})
                inventory[ident][pool + "_response_ids"].append(row["response_id"])
    repository_shared = [
        r for r in pools["shared"] if r.get("scope_kind") == "repository"
    ]
    for item in inventory.values():
        item["actual_target_cost"] = None
        item["actual_target_cost_status"] = "unidentifiable_from_work_scope"
        item["repository_shared_response_ids"] = [
            r["response_id"] for r in repository_shared
        ]
        item["repository_shared_cost"] = _cost_quality(repository_shared, [], end)
        item["repository_shared_cost_basis"] = (
            "associated_repository_overhead_not_allocated_to_target"
        )
        direct = [unique[k] for k in item["direct_response_ids"]]
        item["direct_cost"] = _cost_quality(direct, [], end)
        item["cost_basis"] = "explicit_work_scope_not_causal_cost"
    totals = _cost_quality(responses, events, end)
    pool_rows = {
        key: {
            "response_ids": [r["response_id"] for r in rows],
            **_cost_quality(rows, [], end),
        }
        for key, rows in pools.items()
    }
    assert (
        sum(p["reported_total_tokens"] for p in pool_rows.values())
        == totals["reported_total_tokens"]
    )
    milestone_rows = []
    for level in ("first_green", "current_sealed", "integrated"):
        for count in milestones:
            hit = next((r for r in coverage if r[level + "_contracts"] >= count), None)
            milestone_rows.append(
                {
                    "level": level,
                    "target_contracts": count,
                    "reached": hit is not None,
                    "timestamp": None,
                    "wall_seconds": None,
                    "active_seconds": None,
                    "total_tokens": None,
                    "reported_total_tokens": None,
                    **(hit or {}),
                }
            )
    threshold_rows = []
    for threshold in token_thresholds:
        eligible = [r for r in coverage if r["reported_total_tokens"] <= threshold]
        threshold_rows.append(
            {
                "reported_token_threshold": threshold,
                "coverage": eligible[-1] if eligible else None,
                "basis": "retrospective_reported_response_boundary_not_hard_budget_trial",
            }
        )
    lifecycle = [r for r in events if r["kind"] == "lifecycle"]
    measurement_end = (
        lifecycle[-1]["timestamp"]
        if lifecycle and lifecycle[-1].get("action") in {"stop", "pause"}
        else end
    )
    return {
        "schema_version": 2,
        "manifest": manifest,
        "summary": {
            **snapshot(measurement_end),
            **totals,
            "as_of": end,
            "cutoff": cutoff,
            "run_id": manifest.get("run_id", next(iter(run_ids), None)),
            "target_count": len(inventory),
            "cost_conserved": True,
            "scope_unmatched_responses": sorted(set(scopes) - set(unique)),
            "observed_native_check_count": len(checks_seen),
            "observed_native_check_seconds": sum(
                r.get("duration_seconds", r.get("elapsed_seconds")) or 0
                for r in checks_seen.values()
            ),
            "native_checks_missing_duration": sum(
                r.get("duration_seconds", r.get("elapsed_seconds")) is None
                for r in checks_seen.values()
            ),
        },
        "targets": list(inventory.values()),
        "responses": responses,
        "cost_pools": pool_rows,
        "coverage": coverage,
        "milestones": milestone_rows,
        "token_thresholds": threshold_rows,
        "segments": segments,
        "checks": [
            r for r in events if r["kind"] in {"check_started", "check_finished"}
        ],
        "scope_declarations": [
            r for r in events if r["kind"] in {"work_scope", "response_scope"}
        ],
        "limitations": [
            "Scopes declare work association, not mathematical causal costs.",
            "Theory check associations are shared observations, never exclusive target costs.",
            "Candidate failure counts record unsuccessful submissions, not permanently failed targets.",
            "Helper relations are declared dependencies/consumers, not certified proof citations.",
            "Shared costs are never duplicated into consumer direct costs.",
            "Missing usage and unclosed activity remain unknown; reported tokens are a lower bound.",
            "Coverage validity depends on trusted controller invalidation and acceptance events.",
            "Stage cutoff excludes later work but incorporates recovered provider usage with original timestamps.",
        ],
    }


def write_resource_export(bundle, directory):
    """Write a JSON bundle and independently usable JSON tables."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for key, value in {"resource_metrics": bundle, **bundle}.items():
        (directory / (key + ".json")).write_text(json.dumps(value, indent=2) + "\n")
