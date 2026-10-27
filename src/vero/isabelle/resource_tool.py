"""Agent client for the resource protocol; no proof strategy or retry cap."""

import argparse
import json
import os
import socket
import uuid
from pathlib import Path

from .repository_tool import files


def request(payload, *, timeout_seconds=15):
    """A lost/slow socket never leaves an unbounded polling client behind."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout_seconds)
        try:
            connection.connect(os.environ["FREEFORM_SOCKET"])
            connection.sendall((json.dumps(payload) + "\n").encode())
            with connection.makefile("r") as stream:
                return json.loads(stream.readline())
        except TimeoutError:
            return {
                "status": "transport_timeout",
                "request_id": payload.get("request_id"),
                "acceptance_unknown": True,
                "recover_with": "result",
                "retry_same_request_id": True,
            }


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "command",
        choices=[
            "plan",
            "revise",
            "statement",
            "check",
            "check-theory",
            "seal",
            "integrate",
            "finish",
            "progress",
            "feedback",
            "scope",
            "result",
            "search",
            "query",
            "relate",
        ],
    )
    p.add_argument("arguments", nargs="*")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--characters", type=int, default=12000)
    p.add_argument("--request-id")
    a = p.parse_args()
    if a.command == "search":
        # Catalog results are optional hints, never proof availability evidence.
        query = " ".join(a.arguments).lower()
        rows = []
        with Path(
            os.environ.get("RESOURCE_PREMISES", "/public/premises.jsonl")
        ).open() as stream:
            for line in stream:
                if query in line.lower():
                    rows.append(json.loads(line))
                    if len(rows) >= a.limit:
                        break
        print(
            json.dumps(
                {
                    "catalog_only": True,
                    "native_availability_required": True,
                    "rows": rows,
                }
            )
        )
        return
    payload = {"cmd": a.command, "request_id": a.request_id or uuid.uuid4().hex}
    if a.command in {"plan", "revise", "scope", "relate"}:
        if len(a.arguments) != 1:
            p.error("command requires one JSON file")
        doc = json.loads(Path(a.arguments[0]).read_text())
        if a.command in {"scope", "relate"}:
            payload.update(doc)
        elif a.command == "plan":
            payload["plan"] = doc
        else:
            if set(doc) != {"plan", "reason", "expected_version"}:
                p.error("revision requires plan, reason and expected_version")
            payload.update(doc)
        payload["cmd"] = a.command
    elif a.command in {"check-theory", "seal"}:
        if len(a.arguments) != 1:
            p.error("one theory required")
        payload["theory"] = a.arguments[0]
    elif a.command == "query":
        if len(a.arguments) < 2:
            p.error("query requires target ID and fact names")
        payload.update(target_id=a.arguments[0], facts=a.arguments[1:])
    elif a.command == "check" or (a.command == "statement" and a.arguments):
        payload["target_ids"] = a.arguments
    elif a.command == "feedback":
        if a.arguments:
            payload["target_id"] = a.arguments[0]
        payload.update(offset=a.offset, limit=a.characters)
    elif a.command == "result":
        if len(a.arguments) != 1:
            p.error("one request ID required")
        payload["request_id"] = a.arguments[0]
    if a.command in {
        "statement",
        "check",
        "check-theory",
        "seal",
        "integrate",
        "finish",
        "query",
    }:
        payload["files"] = files()
    print("RESOURCE_REQUEST_ID " + payload["request_id"], flush=True)
    print(json.dumps(request(payload), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
