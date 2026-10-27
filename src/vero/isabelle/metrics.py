"""Offline reported-token/coverage curves from common immutable events.

These are retrospective response-boundary curves, not counterfactual hard-budget
runs. Unreported requests have unknown costs; known token sums are lower bounds.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from .telemetry import TOKEN_FIELDS, EventLog


def _usage_totals(responses):
    fields = (*TOKEN_FIELDS, "total_tokens")
    result = {}
    for key in fields:
        amounts = [(row.get("usage") or {}).get(key) for row in responses]
        result["reported_" + key] = sum(value for value in amounts if value is not None)
        result[key] = (
            result["reported_" + key]
            if all(value is not None for value in amounts)
            else None
        )
    result["uncached_input_tokens"] = (
        result["input_tokens"] - result["cached_input_tokens"]
        if result["input_tokens"] is not None
        and result["cached_input_tokens"] is not None
        else None
    )
    result["reported_responses"] = sum(
        row.get("usage") is not None for row in responses
    )
    result["missing_response_usage"] = sum(
        row.get("usage") is None or row["usage"].get("total_tokens") is None
        for row in responses
    )
    return result


def _interval_seconds(intervals):
    merged = []
    for begin, end in sorted(intervals):
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((begin, max(begin, end)))
    return sum(end - begin for begin, end in merged)


def _activity(events, cutoff):
    starts, intervals, lifecycle = {}, [], []
    unmatched_ends = 0
    for row in events:
        if row["timestamp"] > cutoff or row["kind"] != "lifecycle":
            continue
        lifecycle.append(row)
        key = row.get("segment_id", "run")
        if row.get("action") in {"start", "resume"}:
            starts.setdefault(key, row["timestamp"])
        elif row.get("action") in {"pause", "stop", "end"}:
            if key in starts:
                intervals.append((starts.pop(key), row["timestamp"]))
            else:
                unmatched_ends += 1
    latest_resume = max(
        (row["timestamp"] for row in lifecycle if row.get("action") == "resume"),
        default=float("-inf"),
    )
    terminal = next(
        (
            row["timestamp"]
            for row in reversed(lifecycle)
            if row.get("action") == "stop"
            and row.get("status")
            in {"complete", "retries_exhausted", "budget_exhausted"}
        ),
        float("-inf"),
    )
    # A new resume or a terminal run stop establishes that an older segment's
    # end was lost. Its actual SIGKILL/pause time cannot be reconstructed.
    historical = sum(begin < max(latest_resume, terminal) for begin in starts.values())
    observed = _interval_seconds(intervals)
    if not lifecycle:
        active, quality = None, "unavailable"
    elif historical or unmatched_ends:
        active, quality = None, "incomplete_history"
    else:
        active = _interval_seconds(
            intervals + [(begin, cutoff) for begin in starts.values()]
        )
        quality = "open_segment_assumed_running" if starts else "complete"
    return {
        "active_seconds": active,
        "observed_active_seconds": observed if lifecycle else None,
        "active_time_quality": quality,
        "open_lifecycle_segments": len(starts),
        "unclosed_historical_segments": historical,
        "unmatched_lifecycle_ends": unmatched_ends,
    }


def _quality(events, cutoff):
    rows = [row for row in events if row["timestamp"] <= cutoff]
    started = {
        row["invocation_id"] for row in rows if row["kind"] == "invocation_started"
    }
    ended = {
        row["invocation_id"] for row in rows if row["kind"] == "invocation_finished"
    }
    missing = [
        row
        for row in rows
        if row["kind"] == "usage_missing"
        or (
            row["kind"] == "response_usage"
            and (row.get("usage") is None or row["usage"].get("total_tokens") is None)
        )
    ]
    check_started, check_finished = set(), set()
    for row in rows:
        key = row.get("check_id", row.get("request_id"))
        if key is not None:
            if row["kind"] == "check_started":
                check_started.add(key)
            elif row["kind"] == "check_finished":
                check_finished.add(key)
    return {
        "missing_usage_events": len(missing),
        "active_invocations": len(started - ended),
        "inflight_model_requests": None,
        "pending_checks": len(check_started - check_finished),
        "usage_quality": "reported_only_with_gaps" if missing else "reported_only",
        "inflight_request_observability": "invocations_are_not_individual_requests",
    }


def _cost_quality(responses, events, cutoff):
    result = {**_usage_totals(responses), **_quality(events, cutoff)}
    if result["missing_usage_events"]:
        # Retain a useful recorded lower bound, but never present it as the
        # complete token total of an invocation whose usage is unknown.
        for key in (*TOKEN_FIELDS, "total_tokens", "uncached_input_tokens"):
            result[key] = None
    return result


def export_metrics(
    log, *, token_thresholds=(), milestones=(10, 30, 50, 75, 100), as_of=None
):
    """Return JSON-safe tables; do not mutate experiment state or event files."""
    if not isinstance(log, EventLog):
        log = EventLog.open(log)
    as_of = time.time() if as_of is None else float(as_of)
    events = [row for row in log.events() if row["timestamp"] <= as_of]
    responses = [row for row in events if row["kind"] == "response_usage"]
    # Also defend against manually imported event logs with alternate IDs.
    unique = {}
    for row in responses:
        identity = row.get("response_id")
        if not identity:
            raise ValueError("response event has no response_id")
        if identity in unique and unique[identity].get("usage") != row.get("usage"):
            raise ValueError("conflicting response usage")
        unique.setdefault(identity, row)
    responses = sorted(
        unique.values(), key=lambda row: (row["timestamp"], row["response_id"])
    )
    starts = [
        row["timestamp"]
        for row in events
        if row["kind"] == "lifecycle" and row.get("action") in {"start", "resume"}
    ]
    origin = (
        min(starts)
        if starts
        else min((row["timestamp"] for row in events), default=as_of)
    )
    origin_basis = "lifecycle_start" if starts else "first_observed_event"
    original_ids = set(log.manifest.get("contract_ids", []))
    accepted, helpers = set(), set()
    coverage, ignored = [], []
    for row in events:
        if row["kind"] not in {"contract_accepted", "helper_accepted"}:
            continue
        is_contract = row["kind"] == "contract_accepted"
        identity = row.get("contract_id", row.get("node_id"))
        if not identity or (
            is_contract and original_ids and identity not in original_ids
        ):
            ignored.append(row["event_id"])
            continue
        targets = accepted if is_contract else helpers
        if identity in targets:
            continue
        targets.add(identity)
        when = row["timestamp"]
        activity = _activity(events, when)
        costs = _cost_quality(
            [r for r in responses if r["timestamp"] <= when], events, when
        )
        coverage.append(
            {
                "timestamp": when,
                "node_id": identity,
                "node_kind": "contract" if is_contract else "helper",
                "contracts_green": len(accepted),
                "helpers_green": len(helpers),
                "wall_seconds": max(0, when - origin),
                **activity,
                "evidence_id": row.get("evidence_id"),
                "acceptance_event_id": row["event_id"],
                "time_basis": row.get("time_basis", "committed_acceptance"),
                **costs,
            }
        )
    milestones_out = []
    for target in milestones:
        hit = next((row for row in coverage if row["contracts_green"] >= target), None)
        milestones_out.append(
            {
                "target_contracts": target,
                "reached": hit is not None,
                "timestamp": None,
                "wall_seconds": None,
                "active_seconds": None,
                "observed_active_seconds": None,
                "active_time_quality": "not_reached",
                "total_tokens": None,
                "reported_total_tokens": None,
                **(hit or {}),
            }
        )
    totals = _cost_quality(responses, events, as_of)
    threshold_rows = []
    for budget in token_thresholds:
        eligible = [row for row in coverage if row["reported_total_tokens"] <= budget]
        hit = eligible[-1] if eligible else None
        threshold_rows.append(
            {
                "reported_token_threshold": budget,
                "contracts_green": hit["contracts_green"] if hit else 0,
                "helpers_green": hit["helpers_green"] if hit else 0,
                "budget_observed": budget <= totals["reported_total_tokens"],
                "last_acceptance_timestamp": hit["timestamp"] if hit else None,
                "wall_seconds": hit["wall_seconds"] if hit else None,
                "active_seconds": hit["active_seconds"] if hit else None,
                "observed_active_seconds": hit["observed_active_seconds"]
                if hit
                else None,
                "active_time_quality": hit["active_time_quality"]
                if hit
                else "no_acceptance",
                "usage_quality": hit["usage_quality"]
                if hit
                else _quality(events, as_of)["usage_quality"],
                "interpretation": "retrospective_reported_response_budget_not_hard_budget_trial",
            }
        )
    lifecycle = [row for row in events if row["kind"] == "lifecycle"]
    measurement_end = as_of
    if lifecycle and lifecycle[-1].get("action") == "stop":
        # A terminal run does not acquire more wall time when its report is
        # regenerated days later. A pause still retains the live wall clock.
        measurement_end = lifecycle[-1]["timestamp"]
    activity = _activity(events, measurement_end)
    by_model = {}
    for row in responses:
        key = (row.get("model"), row.get("reasoning_effort"), row.get("role"))
        by_model.setdefault(key, []).append(row)
    model_rows = [
        {"model": k[0], "reasoning_effort": k[1], "role": k[2], **_usage_totals(v)}
        for k, v in by_model.items()
    ]
    summary = {
        "schema_version": 1,
        "run_id": log.run_id,
        "method": log.manifest.get("method"),
        "as_of": as_of,
        "origin": origin,
        "origin_basis": origin_basis,
        "contracts_green": len(accepted),
        "helpers_green": len(helpers),
        "wall_seconds": max(0, measurement_end - origin),
        "measurement_end": measurement_end,
        **activity,
        "ignored_acceptance_events": ignored,
        **totals,
        "limitations": [
            "Unreported requests are unknown cost, never assumed free.",
            "Cached input is part of input; reasoning output is part of output.",
            "A running invocation does not identify the number of in-flight model requests.",
            "Token thresholds use recorded responses and do not simulate online hard stopping.",
            "Active time is a union of lifecycle intervals, not summed parallel agent time.",
            "A lost historical segment end makes active_seconds unknown; observed_active_seconds only counts fully closed intervals.",
            "An open current segment is assumed running and explicitly labelled; its end is not an observed fact.",
        ],
    }
    return {
        "manifest": log.manifest,
        "summary": summary,
        "coverage": coverage,
        "milestones": milestones_out,
        "token_thresholds": threshold_rows,
        "responses": responses,
        "models": model_rows,
    }


def write_export(bundle, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("summary", "manifest"):
        (directory / (name + ".json")).write_text(
            json.dumps(bundle[name], indent=2) + "\n"
        )
    for name in ("coverage", "milestones", "token_thresholds", "responses", "models"):
        rows = bundle[name]
        if name == "responses":
            rows = [{**row, **(row.get("usage") or {})} for row in rows]
        with (directory / (name + ".csv")).open("w", newline="") as stream:
            if not rows:
                continue
            fields = list(dict.fromkeys(key for row in rows for key in row))
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(
                {
                    key: json.dumps(value, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
                for row in rows
            )
    (directory / "metrics.json").write_text(json.dumps(bundle, indent=2) + "\n")
