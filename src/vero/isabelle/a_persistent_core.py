"""Speculative fixed-prefix worker; every GREEN is independently cold-certified."""
import atexit
import hashlib
import json
import re
import subprocess
import time
import uuid
from pathlib import Path


def prefix_parts(source, node_id, imports):
    """Pure identity for live workers and read-only evidence reuse."""
    label = re.search(r'^theory (\S+)', source)[1]
    marker = 'ML \\<open>writeln ("REGEN_TIMING_START ' + node_id + ' '
    prefix = source[:source.index(marker)].split('\nbegin\n', 1)[1]
    identity = hashlib.sha256((prefix + json.dumps(imports) + label).encode()).hexdigest()
    return label, marker, prefix, identity


class PrefixWorker:
    def __init__(self, checker, source, node_id, fact_name, *, server_ml=None):
        self.checker = checker
        self.closed = False
        self.index = 0
        self.name = 'regen-prefix-' + uuid.uuid4().hex[:16]
        self.root = checker.work_dir / self.name
        self.root.mkdir()
        label, marker, prefix, self.identity = prefix_parts(source, node_id, checker.imports())
        for name, value in [('command_timeout',str(getattr(checker, 'persistent_command_timeout', 60))),('prefix',prefix),('imports','\n'.join(checker.imports())),('theory_name',label),('fact_name',fact_name),('line_number',str(source[:source.index(marker)].count('\n')+2))]:
            (self.root/name).write_text(value)
        (self.root/'server.ML').write_bytes((server_ml or Path(__file__).with_suffix('.ML')).read_bytes())
        if server_ml is not None:
            (self.root/'native_retrieval.ML').write_bytes(Path(__file__).with_name('native_retrieval.ML').read_bytes())
        command = checker._command(checker.work_dir/(label+'.thy'), session='Generated_Refine' if checker.previous_theories else checker.base_session,
                                   writable_mounts=[(self.root,'/worker')])
        command[2:2] = ['--name',self.name]
        command = command[:-2] + ['-e','ML_file "/worker/server.ML"']
        self.log = open(self.root/'worker.log','w')
        self.process = subprocess.Popen(command,stdout=self.log,stderr=subprocess.STDOUT,text=True)
        atexit.register(self.close)
        try:
            self.wait(self.root/'ready')
        except Exception:
            self.close()
            raise

    def wait(self, path):
        start = time.monotonic()
        while not path.exists():
            if self.process.poll() is not None:
                raise RuntimeError((self.root/'worker.log').read_text()[-6000:])
            if (time.monotonic()-start > self.checker.timeout
                    or time.monotonic() >= getattr(self.checker, 'request_deadline', float('inf'))):
                self.close()
                raise TimeoutError('persistent checker timeout')
            time.sleep(.05)

    def check(self, body):
        self.index += 1
        start = time.monotonic()
        stem = str(self.index)
        log_offset = (self.root/'worker.log').stat().st_size
        temp = self.root/(stem+'.tmp')
        temp.write_text(body)
        temp.rename(self.root/(stem+'.request'))
        response = self.root/(stem+'.response')
        self.wait(response)
        text = response.read_text()
        with open(self.root/'worker.log','rb') as log:
            log.seek(log_offset)
            output = log.read().decode(errors='replace')
        (self.root/(stem+'.commands.log')).write_text(output)
        return {'verdict':'green' if text=='GREEN' else 'red','returncode':0 if text=='GREEN' else 1,
                'diagnostic':text,'wall_ms':round((time.monotonic()-start)*1000),
                'source':str(self.root/(stem+'.request')),'log':str(response),
                'node_wall_ms':{},'persistent_prefix':self.identity,'speculative':True,
                'command_timings': [{'command':cmd,'wall_ms':int(ms)} for cmd,ms in re.findall(r'^REGEN_COMMAND (\S+) (\d+)$',output,re.M)],
                'command_log':str(self.root/(stem+'.commands.log'))}

    def close(self):
        if self.closed: return
        self.closed = True
        (self.root/'stop').touch()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker','rm','-f',self.name],capture_output=True,timeout=30)
            self.process.wait(timeout=5)
        self.log.close()
        atexit.unregister(self.close)


class IncrementalWorker(PrefixWorker):
    """Advance only to the exact prefix supplied by the committed graph; never on a trial."""
    def __init__(self, checker, source, node_id, fact_name):
        self.label, _, self.prefix, _ = prefix_parts(source,node_id,checker.imports())
        self.imports = tuple(checker.imports())
        self.fact_name = fact_name
        super().__init__(checker,source,node_id,fact_name,
                         server_ml=Path(__file__).with_name('incremental_checker.ML'))

    def align(self, source, node_id, fact_name):
        label,_,prefix,identity=prefix_parts(source,node_id,self.checker.imports())
        if self.closed or self.process.poll() is not None:
            raise RuntimeError('incremental worker exited')
        if label!=self.label or tuple(self.checker.imports())!=self.imports or not prefix.startswith(self.prefix):
            return False
        delta=prefix[len(self.prefix):]
        if delta:
            result=self._request(delta,'advance')
            if result['verdict']!='green':raise RuntimeError('prefix extension failed: '+result['diagnostic'])
            self.prefix=prefix
        self.identity=identity;self.fact_name=fact_name
        marker='ML \\<open>writeln ("REGEN_TIMING_START '+node_id+' '
        (self.root/'line_number').write_text(str(source[:source.index(marker)].count('\n')+2))
        return True

    def _request(self, body, mode):
        stem=str(self.index+1)
        (self.root/(stem+'.mode')).write_text(mode)
        (self.root/(stem+'.fact')).write_text(self.fact_name)
        result=super().check(body)
        output=Path(result['command_log']).read_text()
        result.update(speculative=False,certification='incremental-real-prefix-v1',
            exported_facts=[{'name':bytes.fromhex(n).decode(),'statement':bytes.fromhex(p).decode()}
                for n,p in re.findall(r'^REGEN_FACT_EXPORT ([0-9a-f]+) ([0-9a-f]+)$',output,re.M)])
        return result

    def check(self, body):
        return self._request(body,'check')
