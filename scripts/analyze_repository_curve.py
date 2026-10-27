#!/usr/bin/env python3
"""Read-only retrospective curves from reported response usage and proof evidence.

The collector never reads model reasoning or changes the experimental workspace.
Native completion timestamps are proxies for acceptance, not transaction times.
"""
import argparse
import bisect
import csv
import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
COLLECT = r'''
import json,time
from pathlib import Path
state=json.loads(Path('/results/generation/contract-retries/state.json').read_text())
as_of=time.time()
config=json.loads(Path('/control/config.json').read_text())
responses=[]
for path in Path('/home/agent/.codex/sessions').rglob('*.jsonl'):
 model=config['agent']['model']
 for line in path.open():
  try:event=json.loads(line)
  except ValueError:continue
  payload=event.get('payload',{})
  if event.get('type')=='turn_context':model=payload.get('model',model)
  if event.get('type')=='token_usage_record':
   responses.append({'timestamp':event['timestamp'],'model':model,**{k:payload.get(k) for k in ['thread_id','turn_id','response_id','usage']}})
native={}
for path in Path('/results/generation/contract-retries/native-calls').glob('*.json'):
 row=json.loads(path.read_text())
 if row.get('finished_at') is not None:native.setdefault(row['source'],[]).append(row)
greens=[];missing=[]
for nid,node in state['helper_stages']['nodes'].items():
 if not node['green']:continue
 row=state['contracts'][nid]
 passed=next((h for h in row['history'] if h.get('status')=='proved'),None)
 evidence=passed['evidence'] if passed else row.get('accepted_evidence')
 matches=[n for n in native.get(str(evidence)+'/source',[]) if n.get('status')=='ok']
 if not matches:missing.append(nid);continue
 first=min(matches,key=lambda n:n['finished_at'])
 greens.append({'id':nid,'kind':node['kind'],'timestamp':first['finished_at'],'evidence':evidence,'time_basis':'native_completion_proxy'})
intervals=[]
for path in Path('/results/supervision').glob('call-*.json'):
 row=json.loads(path.read_text())
 if row.get('started_at') is not None:intervals.append({'started_at':row['started_at'],'finished_at':row.get('finished_at',as_of)})
print(json.dumps({'as_of':as_of,'model':config['agent']['model'],'effort':config['agent']['reasoning_effort'],'responses':responses,'greens':greens,'missing_green_timestamps':missing,'runner_intervals':intervals,'finalized':state.get('finalized',False)}))
'''


def seconds(value):
    return float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def timeline(bundle):
    seen = {}
    for row in bundle['responses']:
        if seconds(row['timestamp']) > bundle['as_of']:
            continue
        key = (row['thread_id'], row['response_id'])
        if not key[1]:
            raise ValueError('response identity missing')
        if key in seen and seen[key]['usage'] != row['usage']:
            raise ValueError('conflicting usage for response identity')
        seen[key] = row
    responses = sorted(seen.values(), key=lambda r: seconds(r['timestamp']))
    times, sums = [], [{key: 0 for key in FIELDS}]
    for row in responses:
        times.append(seconds(row['timestamp']))
        sums.append({key: sums[-1][key] + row['usage'].get(key, 0) for key in FIELDS})
    intervals = bundle['runner_intervals']
    start = min(x['started_at'] for x in intervals)
    rows, known, helpers, contracts = [], set(), 0, 0
    for green in sorted(bundle['greens'], key=lambda r: seconds(r['timestamp'])):
        if green['id'] in known:
            continue
        known.add(green['id'])
        helpers += green['kind'] == 'helper'
        contracts += green['kind'] == 'contract'
        t = seconds(green['timestamp'])
        usage = sums[bisect.bisect_right(times, t)]
        rows.append({**green, 'elapsed_seconds': t - start, 'contracts_green': contracts,
                     'helpers_green': helpers, **usage,
                     'total_tokens': usage['input_tokens'] + usage['output_tokens'],
                     'uncached_input_tokens': usage['input_tokens'] - usage['cached_input_tokens'],
                     'usage_basis': 'reported_responses_completed_by_event_time',
                     'inflight_model_requests': 'unknown'})
    return rows, responses


def milestones(rows, thresholds):
    result = []
    for count in thresholds:
        hit = next((r for r in rows if r['contracts_green'] >= count), None)
        result.append({'threshold': count, 'reached': hit is not None,
                       **({k: hit[k] for k in ['timestamp', 'elapsed_seconds', *FIELDS, 'total_tokens', 'time_basis', 'usage_basis']} if hit else {})})
    return result


def budget_points(rows, thresholds, observed_total):
    result = []
    for budget in thresholds:
        eligible = [r for r in rows if r['total_tokens'] <= budget]
        result.append({'total_token_threshold': budget,
                       'contracts_green': max((r['contracts_green'] for r in eligible), default=0),
                       'budget_observed': budget <= observed_total,
                       'interpretation': 'retrospective_reported_usage_not_hard_budget_trial'})
    return result


def write_csv(path, rows):
    if not rows:
        path.write_text('')
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path)
    parser.add_argument('--host', default='serverb')
    parser.add_argument('--container', default='codex-repository-sol-001')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--token-thresholds', type=int, nargs='+', default=[100_000_000, 300_000_000, 500_000_000, 1_000_000_000])
    args = parser.parse_args()
    if args.input:
        bundle = json.loads(args.input.read_text())
    else:
        command = shlex.join(['docker', 'exec', args.container, 'python3', '-c', COLLECT])
        result = subprocess.run(['ssh', '-o', 'ConnectTimeout=10', args.host, command], capture_output=True, text=True, check=True, timeout=60)
        bundle = json.loads(result.stdout)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'snapshot.json').write_text(json.dumps(bundle, indent=2) + '\n')
    rows, responses = timeline(bundle)
    write_csv(args.output / 'coverage-events.csv', rows)
    milestone_rows = milestones(rows, [10, 30, 50, 75, 100])
    write_csv(args.output / 'milestones.csv', milestone_rows)
    write_csv(args.output / 'token-thresholds.csv', budget_points(rows, args.token_thresholds, sum(r['usage'].get('input_tokens', 0) + r['usage'].get('output_tokens', 0) for r in responses)))
    summary = {'as_of': bundle['as_of'], 'reported_responses': len(responses),
               'model': bundle['model'], 'effort': bundle['effort'],
               'missing_green_timestamps': bundle['missing_green_timestamps'],
               'milestones': milestone_rows,
               'limitations': ['Native completion proxies for acceptance timestamps.',
                               'No guarantee that unreported requests or compaction costs are included.',
                               'In-flight requests are not quantified; these are reported-usage curves.',
                               'Elapsed time includes downtime and starts at the first generation runner; planning inside that runner is included, separate preflight/diagnostics are excluded.']}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
