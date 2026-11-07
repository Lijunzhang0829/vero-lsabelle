"""Client for the supervisor-owned freeform experiment checker."""
import argparse
import json
import os
import sys
import socket
import uuid
from pathlib import Path

def request(endpoint, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(endpoint)
        connection.sendall((json.dumps(payload)+"\n").encode())
        with connection.makefile("r") as stream:
            return json.loads(stream.readline())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['check','progress','feedback','result','retry','search'])
    parser.add_argument('arguments', nargs='*')
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    if args.command == 'search':
        query = ' '.join(args.arguments).lower()
        rows = []
        with open('/public/premises.jsonl') as stream:
            for line in stream:
                if query in line.lower():
                    rows.append(json.loads(line))
                    if len(rows) >= args.limit:
                        break
        print(json.dumps(rows,ensure_ascii=False));return
    payload = {'cmd':args.command}
    if args.command == 'check':
        payload.update(contracts=args.arguments,request_id=uuid.uuid4().hex)
        # Freeze before queuing, not after earlier native checks complete.
        root = Path(os.environ.get('FREEFORM_WORKSPACE','/sandbox')).resolve()
        try:
            files = {}
            for path in sorted(root.rglob('*.thy')):
                if path.is_symlink() or path.stat().st_size > 4_000_000:
                    raise ValueError('invalid/symlink/oversized theory source')
                files[path.relative_to(root).as_posix()] = path.read_text()
            payload['files'] = files
        except (OSError, ValueError) as exc:
            payload['source_error'] = str(exc)
        print('FREEFORM_REQUEST_ID '+payload['request_id'],file=sys.stderr,flush=True)
    elif args.command in {'result','retry'}:
        payload['request_id']=args.arguments[0]
    elif args.command == 'feedback' and args.arguments:
        payload['contract']=args.arguments[0]
    result = request(os.environ['FREEFORM_SOCKET'],payload)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if result.get('request_id'):
        request(os.environ['FREEFORM_SOCKET'],{'cmd':'ack','request_id':result['request_id']})


if __name__=='__main__':
    main()
