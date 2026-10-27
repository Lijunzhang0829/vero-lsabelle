"""Durable controller events and common native-call accounting.

The ledger and its transactional outbox commit together. Event publication can
be retried after a lost reply/process crash without inventing acceptance times.
"""

import hashlib
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .benchmark import digest


def implementation_identity():
    root = Path(__file__).parent
    return digest(
        {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".thy", ".ML", ".json", ".md"}
        }
    )


def setup_metrics(root, cfg, bench, method):
    if not cfg.get("metrics"):
        return None
    from .telemetry import initialize_run

    return initialize_run(
        Path(cfg["metrics"]["directory"]), cfg, bench, method, implementation_identity()
    )


@contextmanager
def segment(events, *, resume=False):
    record = {"status": "interrupted"}
    ident = uuid.uuid4().hex
    if events:
        events.emit(
            "lifecycle",
            event_id="segment-start:" + ident,
            action="resume" if resume else "start",
            segment_id=ident,
        )
    try:
        yield record
    except BaseException as exc:
        record.update(status="interrupted", error_type=type(exc).__name__)
        raise
    finally:
        if events:
            events.emit(
                "lifecycle",
                event_id="segment-end:" + ident,
                action="stop"
                if record["status"]
                in {"complete", "retries_exhausted", "budget_exhausted"}
                else "pause",
                segment_id=ident,
                **record,
            )


class MetricsOutbox:
    """Mixin; owner supplies self.state, self.telemetry, and atomic persist()."""

    def record_metric(self, kind, *, event_id, timestamp=None, **fields):
        if self.telemetry is not None:
            self.state.setdefault("metrics_outbox", []).append(
                {
                    "kind": kind,
                    "event_id": event_id,
                    "timestamp": time.time() if timestamp is None else timestamp,
                    **fields,
                }
            )

    def flush_metrics(self):
        if self.telemetry is None:
            return
        for event in self.state.get("metrics_outbox", []):
            self.telemetry.emit(**event)
        # Leaving the just-published outbox on disk is intentional. The next
        # ledger commit removes it; recovery replays its identical event IDs.
        self.state.pop("metrics_outbox", None)

    def record_outcome(self, outcome):
        nid = outcome["id"]
        evidence = outcome["evidence"]
        common = {
            key: outcome.get(key)
            for key in (
                "request_id",
                "attempt",
                "stage",
                "cumulative_attempt",
                "charged",
                "source_sha256",
                "status",
                "infrastructure_retry",
            )
        }
        common.update(
            node_id=nid,
            node_kind="helper" if nid.startswith("h") else "contract",
            evidence_id=evidence,
            evidence_path=evidence,
        )
        if nid.startswith("c"):
            common["contract_id"] = nid
        self.record_metric(
            "candidate_finished", event_id="candidate-finished:" + evidence, **common
        )
        if outcome["status"] == "proved":
            stamp = outcome.setdefault("accepted_at", time.time())
            self.record_metric(
                "helper_accepted" if nid.startswith("h") else "contract_accepted",
                event_id="acceptance:" + evidence,
                timestamp=stamp,
                **common,
            )
        for credit in outcome.get("stage_credits", []):
            self.record_metric(
                "helper_stage_reset",
                event_id="helper-credit:" + str(credit["event_id"]),
                parent_id=credit["parent_id"],
                batch_id=credit["batch_id"],
                stage=credit["stage"],
                remaining_attempts=6,
                previous_attempts=credit["previous_attempts"],
                cumulative_attempts=credit["cumulative_attempts"],
            )


def requires_build(checker):
    return getattr(checker, "config", {}).get("backend") in {
        "a-shared-incremental-v1", "a-shared-batch-v2"
    }


class TraceChecker:
    """Record every native invocation, even a failed process or session build."""

    def __init__(self, checker, events, directory):
        self.native, self.config, self.events = (
            checker,
            getattr(checker, "config", {}),
            events,
        )
        self.directory = Path(directory)

    def _call(self, operation, source, target, **kwargs):
        if self.events is None:
            return getattr(self.native, operation)(source, target, **kwargs)
        ident = uuid.uuid4().hex
        source = Path(source)
        hashes = {
            str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(source.rglob("*"))
            if p.is_file() and p.suffix in {".thy", ".ML"}
        }
        kind = (
            "build"
            if operation == "build_session"
            else "declaration"
            if "helper-registrations" in source.parts or target == "Frozen_Contracts"
            else "seal"
            if "sealing" in source.parts
            else "integration"
            if any(p.startswith("integration-") for p in source.parts)
            or target == "Vero_All"
            else "candidate"
        )
        started = time.time()
        common = {
            "check_id": ident,
            "check_kind": kind,
            "source": str(source),
            "target": target,
            "source_sha256": digest(hashes),
            "operation": operation,
            "timeout_override_seconds": kwargs.get("timeout"),
            "configured_timeout_seconds": self.config.get("timeout_seconds"),
        }
        self.events.emit(
            "check_started",
            event_id="check-start:" + ident,
            timestamp=started,
            **common,
        )
        try:
            result = getattr(self.native, operation)(source, target, **kwargs)
        except BaseException as exc:
            self.events.emit(
                "check_finished",
                event_id="check-end:" + ident,
                status="interrupted",
                elapsed_seconds=time.time() - started,
                error_type=type(exc).__name__,
                **common,
            )
            raise
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (ident + ".log")
        path.write_text(result.get("log", ""))
        self.events.emit(
            "check_finished",
            event_id="check-end:" + ident,
            result={k: v for k, v in result.items() if k != "log"},
            status=result["status"],
            elapsed_seconds=result.get("elapsed_seconds", time.time() - started),
            log_path=str(path),
            **common,
        )
        return result

    def check(self, source, owner, **kwargs):
        return self._call("check", source, owner, **kwargs)

    def build_session(self, source, theories, **kwargs):
        return self._call("build_session", source, theories, **kwargs)

    def close(self):
        if hasattr(self.native, "close"):
            self.native.close()


def checker_description(config):
    if config.get("backend") == "a-shared-batch-v2":
        return (
            f"Checks use {config.get('threads', 4)} Isabelle threads. "
            f"Declaration, prefix startup, cold check and build limits are {config.get('timeout_seconds', 900)} seconds per operation. "
            f"A persistent candidate including its native audit has {config.get('candidate_timeout_seconds', 120)} seconds. "
            "Only checked committed prefixes are reusable; failed candidates are isolated. "
            "Declaration certificates bind the exact relevant source and imported context. "
            "Complete theory seals and final integration require successful audited checks and session builds. "
            "The global resource deadline includes all preparation, checking and fallback work."
        )
    if config.get("backend") == "a-shared-incremental-v1":
        return (
            f"Checks are serialized with {config.get('threads', 8)} Isabelle threads. "
            f"Worker readiness and each proof request have a {config.get('timeout_seconds', 300)}-second limit; "
            f"individual commands in the persistent ML worker have a {config.get('command_timeout_seconds', 60)}-second limit. "
            f"Initial declaration elaboration has a {config.get('initial_statement_timeout_seconds', 1800)}-second limit, "
            f"helper statement checks {config.get('helper_statement_timeout_seconds', 900)} seconds, "
            f"and final repository builds {config.get('integration_build_timeout_seconds', 900)} seconds. "
            "Theory seals use the proof-request limit. "
            "There is no extra combined whole-check cap unless total_timeout_seconds is set. "
            "The shared A backend can fall back after a native worker error to a cold check with its own request limit; "
            "all work and fallback time are recorded under the same submitted candidate. "
            "Only checked, committed prefixes may be reused; failed candidate states do not become premises. "
            "Complete theory seals and repository integration additionally require a successful isolated session build."
        )
    return (
        f"The whole-check limit is {config.get('total_timeout_seconds', 300)} seconds including dependency loading; "
        f"the loading limit is {config.get('dependency_timeout_seconds', 600)} seconds and owner limit is {config.get('timeout_seconds', 300)} seconds. "
        f"Checks are serialized with {config.get('threads', 4)} Isabelle threads. "
        "Successful exact-source evidence can be reused; timed-out processes retain no live state."
    )
