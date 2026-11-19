"""Supervisor-owned normal-runtime clock; no proof strategy or agent control.

All normal work is charged. Exclusions need explicit trusted evidence and apply
only while every experimental worker is blocked. Agent tools only read state.
"""
import fcntl
from functools import wraps
import json
import math
import os
from pathlib import Path
import tempfile
import time
import threading

POLICY = 'normal-wall-minus-confirmed-interruptions-v1'
SCHEMA = 'experiment-budget-v2'
CLOCK_EXCLUSION_REASONS = frozenset({'experiment_pause', 'provider_interruption', 'native_unresponsive', 'offline', 'provider_recovery', 'provider_stream_retry', 'stream_retry'})
REASONS = CLOCK_EXCLUSION_REASONS


def number(value):
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise ValueError('finite nonnegative clock value required')
    return value


def owner_identity():
    return {'pid':os.getpid(), 'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'process_start':Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]}


def owner_alive(owner):
    try:
        return (isinstance(owner,dict) and type(owner['pid']) is int and owner['pid'] > 0
            and owner['boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            and owner['process_start'] == Path('/proc',str(owner['pid']),'stat').read_text().rsplit(')',1)[1].split()[19])
    except (OSError, KeyError, TypeError, ValueError):
        return False


def initial_state(run_id, budget_seconds, started_unix):
    from .budget import duration
    duration(budget_seconds);number(started_unix)
    if not isinstance(run_id,str) or not run_id:
        raise ValueError('nonempty run identity required')
    return {'schema':SCHEMA,'policy':POLICY,'run_id':run_id,'budget_seconds':budget_seconds,
        'started_unix':started_unix,'updated_unix':started_unix,'monotonic_at_update':None,
        'effective_seconds':0.,'wall_seconds':0.,'excluded_seconds':0.,'exclusions':{},
        'history':[],'owner':None,'running':False,'ready':True,'finished':False,
        'accounting_unknown':False}


def validate(data):
    from .budget import duration
    if not isinstance(data,dict) or data.get('schema')!=SCHEMA or data.get('policy')!=POLICY:
        raise ValueError('invalid active clock schema/policy')
    if not isinstance(data.get('run_id'),str) or not data['run_id']:
        raise ValueError('invalid active clock identity')
    duration(data['budget_seconds'])
    for key in ('started_unix','updated_unix','effective_seconds','wall_seconds','excluded_seconds'):
        number(data[key])
    if abs(data['wall_seconds']-data['effective_seconds']-data['excluded_seconds'])>.01:
        raise ValueError('clock totals do not reconcile')
    if any(type(data.get(k)) is not bool for k in ('running','ready','finished','accounting_unknown')):
        raise ValueError('invalid clock lifecycle flags')
    if not isinstance(data['exclusions'],dict) or not isinstance(data['history'],list):
        raise ValueError('invalid clock intervals')
    for item in data['exclusions'].values():
        if item.get('reason') not in REASONS or not isinstance(item.get('evidence'),str) or not item['evidence']:
            raise ValueError('untrusted clock exclusion')
    if data['running']:
        number(data['monotonic_at_update'])


def atomic_state(path, data):
    validate(data)
    path=Path(path)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(data,stream);stream.write('\n');stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),0o444)
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)


def view(data, *, now=None, monotonic=None):
    validate(data)
    now=time.time() if now is None else number(now)
    mono=time.monotonic() if monotonic is None else number(monotonic)
    effective=data['effective_seconds'];excluded=data['excluded_seconds'];wall=data['wall_seconds']
    unknown=data['accounting_unknown'] or (data['running'] and not owner_alive(data['owner']))
    live=data['running'] and not unknown
    if live:
        delta=mono-data['monotonic_at_update']
        if delta<0:raise ValueError('monotonic clock moved backwards')
        wall+=delta
        if data['exclusions']:excluded+=delta
        else:effective+=delta
    if not unknown and not data['running'] and not data['ready'] and not data['finished']:
        gap=now-data['updated_unix']
        number(gap)
        wall+=gap;excluded+=gap
    remaining=None if unknown else max(0,data['budget_seconds']-effective)
    status=('accounting_unknown' if unknown else 'finished' if data['finished'] else
            'exhausted' if remaining==0 else 'available' if live and not data['exclusions'] else
            'not_started' if data['ready'] else 'paused')
    estimate=now+remaining if status=='available' else None
    return {'schema':SCHEMA,'policy':POLICY,'status':status,'run_id':data['run_id'],
        'budget_seconds':data['budget_seconds'],'started_unix':data['started_unix'],
        'observed_unix':now,'effective_seconds':effective,'elapsed_seconds':wall,
        'wall_seconds':wall,'excluded_seconds':excluded,'remaining_seconds':remaining,
        'projected_deadline_unix':estimate,'deadline_unix':None,
        'deadline_kind':'projection_only; exclusions change calendar finish time',
        'accounting_complete':not unknown,'unobserved_tail_seconds':None if unknown else 0,
        'open_exclusions':data['exclusions'],'stop_reason':data.get('finish_reason'),
        'basis':'all normal runtime charged; union of trusted pauses/infra interruptions excluded'}


def serialized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._guard:
            return method(self, *args, **kwargs)
    return guarded


class ActiveClock:
    """One live supervisor owns the lock; caller checkpoints/checks periodically."""
    def __init__(self, path, *, monotonic=time.monotonic, wall_clock=time.time):
        self._guard=threading.RLock()
        self.path=Path(path);self.monotonic=monotonic;self.wall_clock=wall_clock
        self._lock=os.open(str(path)+'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(self._lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.state=json.loads(self.path.read_text());validate(self.state)
            self._last=self.monotonic()
            if self.state['running']:
                if owner_alive(self.state['owner']):raise ValueError('prior clock owner is still alive')
                self.state['accounting_unknown']=True
            elif not self.state['ready'] and not self.state['finished']:
                gap=self.wall_clock()-self.state['updated_unix']
                number(gap)
                self.state['wall_seconds']+=gap;self.state['excluded_seconds']+=gap
                self._event('offline_recovery',seconds=gap,evidence='prior clean clock closure')
            self.closed=False
        except BaseException:
            os.close(self._lock);raise

    def _event(self, action, **fields):
        self.state['history'].append(dict(action=action,at_unix=self.wall_clock(),effective_seconds=self.state['effective_seconds'],wall_seconds=self.state['wall_seconds'],excluded_seconds=self.state['excluded_seconds'],**fields))

    def _write(self):
        self.state.update(updated_unix=self.wall_clock(),monotonic_at_update=self._last)
        atomic_state(self.path,self.state)

    def _settle(self):
        if self.closed:raise ValueError('clock closed')
        now=self.monotonic();delta=now-self._last
        if not math.isfinite(delta) or delta<0:raise ValueError('monotonic clock moved backwards')
        if self.state['accounting_unknown']:raise ValueError('crash tail requires evidence-backed reconciliation')
        if self.state['running']:
            self.state['wall_seconds']+=delta
            key='excluded_seconds' if self.state['exclusions'] else 'effective_seconds'
            self.state[key]+=delta
        self._last=now

    @serialized
    def resume(self):
        if self.closed or self.state['finished']:raise ValueError('closed/finished clock cannot resume')
        self._settle()
        if self.state['effective_seconds']>=self.state['budget_seconds']:raise ValueError('budget exhausted')
        if self.state['ready']:
            self.state['started_unix']=self.wall_clock()
        self.state.update(running=True,ready=False,owner=owner_identity())
        self.state['exclusions'].pop('controller-offline',None)
        self._event('resume');self._write()

    @serialized
    def exclude(self, identity, reason, evidence, *, all_workers_blocked=False):
        if self.state['finished']:raise ValueError('clock finished')
        if reason not in REASONS or not isinstance(evidence,str) or not evidence.strip() or all_workers_blocked is not True:
            raise ValueError('exclusion requires confirmed all-worker interruption and evidence')
        if not isinstance(identity,str) or not identity or identity=='controller-offline':raise ValueError('invalid exclusion identity')
        self._settle()
        item={'reason':reason,'evidence':evidence}
        if identity in self.state['exclusions']:
            if self.state['exclusions'][identity]!=item:raise ValueError('conflicting exclusion replay')
            self._write();return
        if any(e.get('identity')==identity for e in self.state['history']):raise ValueError('exclusion identity already completed')
        self.state['exclusions'][identity]=item
        self._event('exclude_started',identity=identity,**item);self._write()

    @serialized
    def end_exclusion(self, identity, evidence):
        if self.state['finished']:raise ValueError('clock finished')
        if not isinstance(evidence,str) or not evidence.strip():raise ValueError('recovery evidence required')
        self._settle()
        if identity not in self.state['exclusions']:raise ValueError('unknown exclusion')
        self.state['exclusions'].pop(identity)
        self._event('exclude_finished',identity=identity,evidence=evidence);self._write()

    @serialized
    def reconcile(self, active_seconds, evidence):
        """Trusted recovery supplies actual charged tail; no automatic refund."""
        if self.closed or not self.state['accounting_unknown'] or not isinstance(evidence,str) or not evidence.strip():
            raise ValueError('unknown tail and recovery evidence required')
        gap=self.wall_clock()-self.state['updated_unix'];number(gap);number(active_seconds)
        if active_seconds>gap:raise ValueError('active crash tail exceeds unobserved interval')
        self.state['wall_seconds']+=gap;self.state['effective_seconds']+=active_seconds
        self.state['excluded_seconds']+=gap-active_seconds
        self.state.update(accounting_unknown=False,running=False,owner=None)
        self._last=self.monotonic()
        self._event('crash_reconciled',active_tail_seconds=active_seconds,excluded_tail_seconds=gap-active_seconds,evidence=evidence)
        self._write()

    @serialized
    def check(self):
        if self.closed:raise ValueError('clock closed')
        self._settle();self._write()
        return not self.state['finished'] and self.state['effective_seconds']<self.state['budget_seconds']

    @serialized
    def snapshot(self):
        if not self.closed and not self.state['accounting_unknown']:self.check()
        return view(self.state,now=self.wall_clock(),monotonic=self.monotonic())

    @serialized
    def finish(self, reason):
        if not isinstance(reason,str) or not reason:raise ValueError('stop reason required')
        if self.state['finished']:
            if self.state.get('finish_reason')!=reason:raise ValueError('conflicting finish replay')
            return
        self._settle();self.state.update(finished=True,running=False,owner=None,finish_reason=reason)
        self._event('finish',reason=reason);self._write()

    @serialized
    def close(self):
        if self.closed:return
        if not self.state['accounting_unknown']:
            self._settle()
            if not self.state['finished']:
                self.state['exclusions']['controller-offline']={'reason':'offline','evidence':'clean supervisor closure'}
            self.state.update(running=False,owner=None)
            self._event('close');self._write()
        os.close(self._lock);self.closed=True
