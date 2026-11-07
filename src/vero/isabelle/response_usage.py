"""Continuously copy only accounting metadata from bound Codex session logs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .telemetry import TOKEN_FIELDS, normalize_usage, timestamp


class ResponseUsageCollector:
    def __init__(
        self,
        log,
        *,
        codex_home,
        cli_events,
        invocation_id,
        model,
        reasoning_effort,
        role="repository_agent",
        accounting_scope=None,
        exclusive_home=False,
    ):
        self.log = log
        self.home = Path(codex_home)
        self.cli_events = Path(cli_events)
        self.invocation_id = invocation_id
        self.model = model
        self.effort = reasoning_effort
        self.role = role
        self.exclusive_home = bool(exclusive_home)
        self.auxiliary_threads = set()
        self.accounting_scope = dict(accounting_scope or {})
        allowed_scope = {
            "target_ids",
            "theory_ids",
            "phase",
            "attribution_basis",
            "confidence",
            "scope_kind",
        }
        if set(self.accounting_scope) - allowed_scope:
            raise ValueError("unsupported accounting scope field")
        for key in ("target_ids", "theory_ids"):
            if key in self.accounting_scope and (
                not isinstance(self.accounting_scope[key], list)
                or any(
                    not isinstance(v, str) or not v for v in self.accounting_scope[key]
                )
            ):
                raise ValueError("scope identities must be lists of nonempty strings")
        self.offsets = {}
        self.contexts = {}
        self.threads = set()
        self.thread_roles = {}
        self.turns_started = 0
        self.turns_completed = 0
        self.cli_usage = {}
        self.records = {}
        self.warnings = 0
        self.warning_ids = set()
        self.invocations = {}
        self.bindings = {}
        for event in self.log.events():
            if event["kind"] == "invocation_started":
                self.invocations[event["invocation_id"]] = event
            elif event["kind"] == "invocation_thread":
                self.bindings.setdefault(event["thread_id"], set()).add(
                    event["invocation_id"]
                )
            elif event["kind"] == "auxiliary_session" and self.exclusive_home:
                self.auxiliary_threads.add(event["thread_id"])
                self.threads.add(event["thread_id"])
                self.thread_roles[event["thread_id"]] = "auxiliary_session"
            elif (
                event["kind"] == "response_usage"
                and event.get("invocation_id") == self.invocation_id
            ):
                self.records[event["event_id"]] = event

    def _lines(self, path):
        if path.is_symlink():
            return
        stat = path.stat()
        previous = self.offsets.get(path)
        offset = (
            previous[1]
            if previous and previous[0] == stat.st_ino and stat.st_size >= previous[1]
            else 0
        )
        with path.open("rb") as stream:
            stream.seek(offset)
            while True:
                position = stream.tell()
                line = stream.readline()
                if not line.endswith(b"\n"):
                    break  # Never consume a partially flushed record.
                self.offsets[path] = (stat.st_ino, stream.tell())
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    self._warning("invalid_session_record", str(path), position)
                    continue
                if isinstance(event, dict):
                    yield position, event

    def _warning(self, reason, source, position=None):
        identity = hashlib.sha256(
            f"{self.invocation_id}:{source}:{position}:{reason}".encode()
        ).hexdigest()
        if identity in self.warning_ids:
            return
        self.warning_ids.add(identity)
        self.warnings += 1
        self.log.emit(
            "usage_missing",
            event_id="usage-missing:" + identity,
            invocation_id=self.invocation_id,
            reason=reason,
            usage=None,
            source=source,
            source_offset=position,
        )

    def bind(self, thread_id, *, parent_thread_id=None):
        if not isinstance(thread_id, str) or not thread_id:
            return
        if thread_id in self.threads and thread_id not in self.auxiliary_threads:
            return
        self.auxiliary_threads.discard(thread_id)
        self.threads.add(thread_id)
        self.thread_roles[thread_id] = "subagent" if parent_thread_id else self.role
        self.bindings.setdefault(thread_id, set()).add(self.invocation_id)
        self.log.emit(
            "invocation_thread",
            event_id=f"thread:{self.invocation_id}:{thread_id}",
            invocation_id=self.invocation_id,
            thread_id=thread_id,
            parent_thread_id=parent_thread_id,
            role=self.thread_roles[thread_id],
        )

    def _adopt_auxiliary(self, thread_id, *, parent_thread_id=None):
        if not isinstance(thread_id, str) or not thread_id or thread_id in self.threads:
            return
        self.threads.add(thread_id)
        self.auxiliary_threads.add(thread_id)
        self.thread_roles[thread_id] = "auxiliary_session"
        self.log.emit(
            "auxiliary_session",
            event_id="auxiliary-session:" + thread_id,
            thread_id=thread_id,
            observed_parent_thread_id=parent_thread_id,
            membership_basis="exclusive_run_home",
            invocation_id=None,
        )
        # We have provider responses, but not this auxiliary CLI's invocation
        # end. Keep its possible unreported tail explicit rather than free.
        self.log.emit(
            "usage_missing",
            event_id="auxiliary-session-boundary:" + thread_id,
            thread_id=thread_id,
            invocation_id=None,
            usage=None,
            reason="auxiliary_session_completion_unobserved",
        )
        self.warnings += 1

    def _owner(self, thread_id, when):
        candidates = [
            self.invocations[key]
            for key in self.bindings.get(thread_id, set())
            if key in self.invocations and self.invocations[key]["timestamp"] <= when
        ]
        return max(candidates, key=lambda row: row["timestamp"]) if candidates else None

    def _session_event(self, path, position, event):
        kind = event.get("type")
        context = self.contexts.setdefault(
            path,
            {
                "model": self.model,
                "reasoning_effort": self.effort,
                "model_source": "requested_default",
            },
        )
        # No prompt, tool output, response text, or reasoning text is retained.
        if kind not in {"session_meta", "turn_context", "token_usage_record"}:
            return
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            return
        if kind == "session_meta":
            context["thread_id"] = payload.get("id", payload.get("thread_id"))
            if context["thread_id"] in self.auxiliary_threads:
                context.update(
                    model=None,
                    reasoning_effort=None,
                    model_source="unknown_auxiliary_session",
                )
            return
        if kind == "turn_context":
            context["model"] = payload.get("model", context["model"])
            if payload.get("model"):
                context["model_source"] = "turn_context"
            context["reasoning_effort"] = payload.get(
                "effort", payload.get("reasoning_effort", context["reasoning_effort"])
            )
            return
        thread_id = payload.get("thread_id", context.get("thread_id"))
        if thread_id not in self.threads:
            return
        response_id = payload.get("response_id")
        if not response_id or event.get("timestamp") is None:
            self._warning("response_identity_or_timestamp_missing", str(path), position)
            return
        when = timestamp(event["timestamp"])
        usage = normalize_usage(payload.get("usage"))
        event_id = "response:" + str(response_id)
        previous = self.log.get(event_id)
        if previous:
            if previous.get("usage") != usage:
                raise ValueError(
                    "conflicting usage for response identity " + str(response_id)
                )
            if previous.get("invocation_id") == self.invocation_id:
                self.records[event_id] = previous
            return
        owner = self._owner(thread_id, when)
        auxiliary = thread_id in self.auxiliary_threads
        row = self.log.emit(
            "response_usage",
            event_id=event_id,
            timestamp=when,
            invocation_id=owner["invocation_id"] if owner else None,
            thread_id=thread_id,
            turn_id=payload.get("turn_id"),
            response_id=response_id,
            root_turn_id=payload.get("root_turn_id"),
            session_id=payload.get("session_id"),
            model=context["model"],
            reasoning_effort=context["reasoning_effort"],
            requested_model=owner.get("requested_model", self.model)
            if owner
            else None
            if auxiliary
            else self.model,
            model_source=context["model_source"],
            role=self.thread_roles.get(thread_id, self.role),
            usage=usage,
            usage_status=(
                "reported"
                if usage and usage["total_tokens"] is not None
                else "incomplete"
                if usage
                else "missing"
            ),
            source=str(path),
            source_offset=position,
            attribution="invocation_interval" if owner else "historical_unattributed",
            **(
                owner.get("accounting_scope", {})
                if owner
                else {
                    "scope_kind": "repository",
                    "target_ids": [],
                    "theory_ids": [],
                    "phase": "auxiliary_session",
                    "attribution_basis": "exclusive_run_home_session",
                    "confidence": "run_membership_not_parentage",
                }
                if auxiliary
                else {}
            ),
        )
        if row.get("invocation_id") == self.invocation_id:
            self.records[event_id] = row

    def poll(self):
        if self.cli_events.exists():
            for _, event in self._lines(self.cli_events):
                kind = event.get("type")
                if kind == "thread.started":
                    self.bind(event.get("thread_id"))
                elif kind == "turn.started":
                    self.turns_started += 1
                elif kind == "turn.completed":
                    self.turns_completed += 1
                    # Only diagnostic; never combine these with response usage.
                    for key, value in (event.get("usage") or {}).items():
                        if isinstance(value, int) and not isinstance(value, bool):
                            self.cli_usage[key] = self.cli_usage.get(key, 0) + value
        if not self.threads and not self.exclusive_home:
            return
        files = []
        for folder in (self.home / "sessions", self.home / "archived_sessions"):
            if folder.is_symlink() and self.exclusive_home:
                self._warning("unreadable_exclusive_session_directory", str(folder))
            if folder.exists() and not folder.is_symlink():
                files.extend(sorted(folder.rglob("*.jsonl")))
        metadata = {}
        for path in files:
            if path.is_symlink():
                if self.exclusive_home:
                    self._warning("unreadable_exclusive_session_symlink", str(path))
                continue
            try:
                with path.open("rb") as stream:
                    first = stream.readline()
                meta = json.loads(first)
                payload = meta.get("payload") or {}
                if (
                    meta.get("type") != "session_meta"
                    or not isinstance(payload, dict)
                    or not isinstance(payload.get("id", payload.get("thread_id")), str)
                    or not payload.get("id", payload.get("thread_id"))
                ):
                    raise ValueError("invalid session metadata")
            except (ValueError, UnicodeDecodeError, OSError, AttributeError):
                if self.exclusive_home:
                    self._warning("unreadable_exclusive_session_metadata", str(path))
                continue
            metadata[path] = payload

        # Session source nesting changes between CLI versions. Only the explicit
        # parent-thread metadata key is used; no model-authored text is inspected.
        def parents(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "parent_thread_id" and isinstance(child, str):
                        yield child
                    elif isinstance(child, (dict, list)):
                        yield from parents(child)
            elif isinstance(value, list):
                for child in value:
                    yield from parents(child)

        if self.exclusive_home:
            for meta in metadata.values():
                thread = meta.get("id", meta.get("thread_id"))
                if thread in self.bindings and thread not in self.threads:
                    self.threads.add(thread)
                elif not list(parents(meta.get("source", {}))):
                    self._adopt_auxiliary(thread)
        changed = True
        while changed:
            changed = False
            for meta in metadata.values():
                thread = meta.get("id", meta.get("thread_id"))
                parent = next(
                    (p for p in parents(meta.get("source", {})) if p in self.threads),
                    None,
                )
                if parent and thread not in self.threads:
                    if parent in self.auxiliary_threads:
                        self._adopt_auxiliary(thread, parent_thread_id=parent)
                    else:
                        self.bind(thread, parent_thread_id=parent)
                    changed = True
        for path, meta in metadata.items():
            thread = meta.get("id", meta.get("thread_id"))
            if self.exclusive_home and thread not in self.threads:
                self._adopt_auxiliary(
                    thread, parent_thread_id=next(parents(meta.get("source", {})), None)
                )
            if thread not in self.threads:
                source = meta.get("source", {})
                if (
                    isinstance(source, dict) and "subagent" in source
                ) or source == "subagent":
                    self._warning("unattributed_subagent_session", str(path))
                continue
            for position, event in self._lines(path):
                self._session_event(path, position, event)

    def finish(self, status):
        self.poll()
        incomplete = any(
            row.get("usage") is None or row["usage"].get("total_tokens") is None
            for row in self.records.values()
        )
        missing = (
            not self.records
            or incomplete
            or not self.turns_completed
            or self.turns_started > self.turns_completed
            or status != "completed"
        )
        if missing:
            self.log.emit(
                "usage_missing",
                event_id="usage-boundary:" + self.invocation_id,
                invocation_id=self.invocation_id,
                usage=None,
                reason=(
                    "no_reported_response_usage"
                    if not self.records
                    else "incomplete_reported_response_usage"
                    if incomplete
                    else "interrupted_invocation_may_have_unreported_usage"
                ),
            )
        return self.summary(missing=missing)

    def summary(self, *, missing=False):
        rows = [
            row["usage"]
            for row in self.records.values()
            if row.get("usage") is not None
        ]
        totals = {}
        for key in (*TOKEN_FIELDS, "total_tokens"):
            known = [row[key] for row in rows if row.get(key) is not None]
            totals[key] = sum(known) if rows and len(known) == len(rows) else None
        return {
            "usage": totals,
            "reported_responses": len(rows),
            "usage_complete": not missing
            and bool(rows)
            and not self.warnings
            and not self.auxiliary_threads
            and all(row.get("total_tokens") is not None for row in rows),
            "unreported_usage_possible": missing
            or bool(self.warnings)
            or bool(self.auxiliary_threads),
            "auxiliary_sessions": len(self.auxiliary_threads),
            "cli_turn_usage": self.cli_usage,
            "accounting_basis": "individual_session_token_usage_record",
        }


def reconcile_usage(log, *, codex_home):
    """Recover usage after a killed collector without resuming a model process.

    Only accounting events are added. Existing invocation identities and response
    source timestamps are retained; an absent invocation end remains absent.
    """
    from .telemetry import EventLog

    if not isinstance(log, EventLog):
        log = EventLog.open(log)
    before = log.events()
    invocations = [row for row in before if row["kind"] == "invocation_started"]
    finished = {
        row["invocation_id"]: row
        for row in before
        if row["kind"] == "invocation_finished"
    }
    missing_invocations = {
        row.get("invocation_id") for row in before if row["kind"] == "usage_missing"
    }
    bindings = [row for row in before if row["kind"] == "invocation_thread"]
    reports = []
    initial_responses = sum(row["kind"] == "response_usage" for row in before)
    for invocation in invocations:
        ident = invocation["invocation_id"]
        collector = ResponseUsageCollector(
            log,
            codex_home=codex_home,
            cli_events=Path(invocation["source"]) / "agent_events.jsonl",
            invocation_id=ident,
            model=invocation.get("requested_model", invocation.get("model")),
            reasoning_effort=invocation.get("reasoning_effort"),
            role=invocation.get("role", "repository_agent"),
            accounting_scope=invocation.get("accounting_scope"),
            exclusive_home=invocation.get("accounting_exclusive_home", False),
        )
        for bound in bindings:
            if bound["invocation_id"] == ident:
                collector.bind(
                    bound["thread_id"], parent_thread_id=bound.get("parent_thread_id")
                )
        collector.poll()
        unclosed = ident not in finished
        if unclosed:
            log.emit(
                "usage_missing",
                event_id="unclosed-invocation:" + ident,
                invocation_id=ident,
                usage=None,
                reason="invocation_end_unobserved_unreported_cost_possible",
            )
        terminal = finished.get(ident, {})
        unknown = (
            unclosed
            or ident in missing_invocations
            or terminal.get("status") != "completed"
            or bool((terminal.get("accounting") or {}).get("unreported_usage_possible"))
            or not collector.records
            or any(
                row.get("usage") is None or row["usage"].get("total_tokens") is None
                for row in collector.records.values()
            )
        )
        report = collector.summary(missing=unknown)
        reports.append(
            {"invocation_id": ident, "invocation_end_observed": not unclosed, **report}
        )
    final_responses = sum(row["kind"] == "response_usage" for row in log.events())
    return {
        "run_id": log.run_id,
        "invocations_scanned": len(invocations),
        "new_response_events": final_responses - initial_responses,
        "total_response_events": final_responses,
        "invocations": reports,
        "proof_state_modified": False,
        "note": "Missing provider usage and invocation end times remain unknown.",
    }
