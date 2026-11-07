"""Basic client for constrained autonomous repository proof generation."""

import argparse
import json
import os
import socket
import sys
import uuid
from pathlib import Path


def request(payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(os.environ["FREEFORM_SOCKET"])
        connection.sendall((json.dumps(payload) + "\n").encode())
        with connection.makefile("r") as stream:
            return json.loads(stream.readline())


def files():
    root = Path(os.environ.get("FREEFORM_WORKSPACE", "/sandbox")).resolve()
    result = {}
    for path in sorted(root.rglob("*.thy")):
        if (
            path.is_symlink()
            or any(p.is_symlink() for p in path.parents if p != root.parent)
            or path.stat().st_size > 4_000_000
        ):
            raise ValueError("symlink or oversized theory source")
        result[path.relative_to(root).as_posix()] = path.read_text()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "check",
            "helpers",
            "depend",
            "progress",
            "feedback",
            "search",
            "result",
            "retry",
            "finish",
        ],
    )
    parser.add_argument("arguments", nargs="*")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if args.command == "search":
        query = " ".join(args.arguments).lower()
        rows = []
        with open("/public/premises.jsonl") as stream:
            for line in stream:
                if query in line.lower():
                    rows.append(json.loads(line))
                    if len(rows) >= args.limit:
                        break
        print(json.dumps(rows, ensure_ascii=False))
        return
    payload = {"cmd": args.command}
    if args.command == "check":
        payload.update(contracts=args.arguments, request_id=uuid.uuid4().hex)
        try:
            payload["files"] = files()
        except (OSError, ValueError) as exc:
            payload["source_error"] = str(exc)
    elif args.command == "helpers":
        if len(args.arguments) != 1:
            parser.error("helpers requires one JSON file")
        payload.update(json.loads(Path(args.arguments[0]).read_text()))
        payload["cmd"] = "helpers"
        payload["files"] = files()
    elif args.command == "depend":
        if len(args.arguments) < 2:
            parser.error("depend requires a parent and dependency IDs")
        payload.update(parent=args.arguments[0], dependencies=args.arguments[1:])
    elif args.command in {"result", "retry"}:
        if len(args.arguments) != 1:
            parser.error("result/retry requires one request ID")
        payload["request_id"] = args.arguments[0]
    elif args.command == "feedback" and args.arguments:
        payload["contract"] = args.arguments[0]
    if payload.get("request_id"):
        print(
            "REPOSITORY_REQUEST_ID " + payload["request_id"],
            file=sys.stderr,
            flush=True,
        )
    result = request(payload)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if (
        result.get("request_id")
        and "registered" not in result
        and args.command != "helpers"
    ):
        request({"cmd": "ack", "request_id": result["request_id"]})


if __name__ == "__main__":
    main()
