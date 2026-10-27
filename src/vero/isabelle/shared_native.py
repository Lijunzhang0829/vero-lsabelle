"""A's frozen persistent protocol with a local, isolated Isabelle launcher.

The copied Python/ML cores are verified before use. Frontends still own source
construction, proposition audits and acceptance: a worker GREEN alone never
replaces VERO's native audit marker. No Docker socket or shared writable heap is
needed in the experiment container.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import pwd
import re
import subprocess
import time
import uuid
from pathlib import Path

from .a_persistent_core import IncrementalWorker, prefix_parts
from .freeform_sources import tokens
from .processes import kill_tree

BACKEND = "a-shared-incremental-v1"
CORE_DIR = Path(__file__).parent
CORE_MANIFEST = CORE_DIR / "a_shared_core.json"
GATE_BEGIN = "(* VERO_REPOSITORY_COMMAND_GATE_BEGIN *)"
GATE_END = "(* VERO_REPOSITORY_COMMAND_GATE_END *)"
TARGET_ID = "native_target"
# The requested theorem is certified by the frontend's trusted native audit.
# A's post-check consolidation may use a stable parent fact for locale-local
# targets whose exported global binding differs from their local source name.
CERTIFICATION_FACT = "Pure.reflexive"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def core_identity():
    manifest = json.loads(CORE_MANIFEST.read_text())
    for name, entry in manifest["files"].items():
        if hashlib.sha256((CORE_DIR / name).read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("shared Isabelle core changed: " + name)
    return digest(manifest)


def audit_markers(log):
    return [cid + ':' + polarity for cid, polarity in re.findall(
        r'^VERO_RESULT ([ch]\d{4}) (prove|disprove) clean$', log, re.MULTILINE)]


def ml_string(value):
    return '"' + ''.join(
        '\\' + str(byte).zfill(3) if byte < 32 or byte > 126
        else '\\' + chr(byte) if byte in (34, 92) else chr(byte)
        for byte in str(value).encode()
    ) + '"'


def split_gate(source):
    if GATE_BEGIN not in source:
        return source, ""
    pattern = re.escape(GATE_BEGIN) + r".*?" + re.escape(GATE_END)
    matches = list(re.finditer(pattern, source, re.DOTALL))
    if len(matches) != 1:
        raise ValueError("ambiguous native command gate")
    item = matches[0]
    return source[:item.start()] + source[item.end():], item.group()


def theory_parts(source, expected):
    ts = tokens(source)
    if len(ts) < 5 or [t.value for t in ts[:3]] != ["theory", expected, "imports"]:
        raise ValueError("unsupported theory header")
    begin = next((i for i, token in enumerate(ts[3:], 3)
                  if not token.quoted and token.value == "begin"), None)
    if begin is None:
        raise ValueError("theory begin is missing")
    if ts[-1].quoted or ts[-1].value != "end":
        raise ValueError("incomplete theory")
    import_end = next((i for i in range(3, begin)
                       if not ts[i].quoted and ts[i].value == "keywords"), begin)
    imports = [token.value for token in ts[3:import_end]]
    return imports, source[ts[begin].end:ts[-1].start]


def source_request(directory, owner):
    """Bind local import closure and separate the last submitted declaration.

    Freeform and slot frontends insert these markers themselves. Unknown source
    layouts are handled by the ordinary full process checker, never guessed.
    """
    directory = Path(directory).resolve()
    source = (directory / (owner + ".thy")).read_text()
    imports, body = theory_parts(source, owner)
    if any(not token.quoted and token.value == "contract_reference" for token in tokens(body)):
        raise ValueError("complete_theory_or_statement_check")
    local, visited, gate_blocks = {}, set(), []

    def visit(name):
        if name in visited:
            return
        visited.add(name)
        path = directory / (name + ".thy")
        if not path.exists():
            return
        if path.is_symlink() or path.parent != directory:
            raise ValueError("local import is outside immutable source directory")
        text, gate = split_gate(path.read_text())
        dependencies, _ = theory_parts(text, name)
        for dependency in dependencies:
            visit(dependency)
        local[name] = text
        if gate:
            gate_blocks.append(gate)

    for name in imports:
        visit(name)
    freeform = list(re.finditer(
        r'ML\s+\\<open>writeln\s+"FREEFORM_TARGET_BEGIN\s+([ch]\d{4})"\\<close>', body))
    slots = list(re.finditer(r'(?m)^\s*vero_(?:prove|disprove)\s+"([ch]\d{4})"', body))
    marker = freeform[-1] if freeform else slots[-1] if slots else None
    # Statement checks, full-theory seals and integration are deliberately cold.
    if marker is None:
        raise ValueError("complete_theory_or_statement_check")
    prefix, candidate = body[:marker.start()], body[marker.start():]
    polarity = 'prove'
    if not freeform:
        polarity = 'disprove' if 'vero_disprove' in marker[0] else 'prove'
        slot = re.search(r'\(\* VERO START ' + marker[1] + ' ' + polarity
                         + r' \*\)(.*?)\(\* VERO END ', candidate, re.DOTALL)
        if slot is None or slot[1].strip() in {'', 'oops'}:
            raise ValueError('complete_theory_or_statement_check')
    # The frontend adds one blank line before the current target marker. That
    # separator is absent when the same declaration becomes a committed prefix.
    # Normalize only terminal whitespace, preserving every command/token.
    prefix = prefix.rstrip() + '\n'
    # A registered prefix includes context, accepted nodes, and erased oops
    # declarations. Audited target facts still come exclusively from the trial.
    synthetic = ('theory ' + owner + '\nimports ' + ' '.join(imports)
                 + '\nbegin\n' + prefix + 'ML \\<open>writeln ("REGEN_TIMING_START '
                 + TARGET_ID + ' " ^ Time.toString (Time.now ()))\\<close>\n')
    depth = sum(1 if t.value == 'begin' else -1 if t.value == 'end' else 0
                for t in tokens(prefix) if not t.quoted)
    return {"owner": owner, "imports": imports, "sources": local,
            "source": source, "synthetic": synthetic, "candidate": candidate,
            "prefix_depth": depth, "gate": '\n'.join(gate_blocks),
            "target": marker[1], "polarity": polarity, "closure_sha256": digest(local)}


class WorkerContext:
    """The small checker ABI consumed by A's unchanged Python core."""
    def __init__(self, config, imports, root, deadline):
        self.config, self._imports, self.work_dir = config, imports, root
        self.timeout = config.get("timeout_seconds", 300)
        self.request_deadline = deadline

    def imports(self):
        return self._imports


def checker_owned(path, config, mode):
    """Give the isolated checker access without depending on root's umask."""
    path.chmod(mode)
    prefix = config.get("command_prefix", [])
    if prefix[:2] == ["runuser", "-u"] and os.geteuid() == 0:
        account = pwd.getpwnam(prefix[2])
        os.chown(path, account.pw_uid, account.pw_gid)
    return path


def owned_directory(path, config):
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    return checker_owned(path, config, 0o700)


def owned_file(path, value, config):
    path.write_text(value)
    return checker_owned(path, config, 0o600)


def process_command(config):
    command = list(config.get("command_prefix", [])) + [
        config.get("isabelle", "/workspace/verification/isabelle/bin/isabelle"),
        "process", "-o", "threads=" + str(config.get("threads", 8)),
        "-l", config.get("session", "Semantic_Base")]
    for path in config.get("session_dirs", []):
        command += ["-d", str(path)]
    return command


class LocalWorker(IncrementalWorker):
    """Only launch and teardown differ from A; request/advance code is shared."""
    core_filename = "a_incremental_core.ML"

    def protocol_files(self, config):
        return {
            "command_timeout": str(config.get("command_timeout_seconds", 60)),
            "prefix": self.prefix, "imports": "\n".join(self.imports),
            "theory_name": self.label, "fact_name": self.fact_name,
            "line_number": "1",
        }

    def __init__(self, config, request, root, deadline, process_registry=None):
        self.closed, self.index = False, 0
        self.name = "native-" + uuid.uuid4().hex
        self.root = owned_directory(root / self.name, config)
        self.checker = WorkerContext(config, request["imports"], self.root, deadline)
        self.label, _, self.prefix, self.identity = prefix_parts(
            request["synthetic"], TARGET_ID, request["imports"])
        self.imports = tuple(request["imports"])
        self.fact_name = CERTIFICATION_FACT
        self.binding = request["closure_sha256"]
        self.prefix_depth = request["prefix_depth"]
        source_dir = owned_directory(self.root / "sources", config)
        for name, text in request["sources"].items():
            owned_file(source_dir / (name + '.thy'), text, config)
        for name, value in self.protocol_files(config).items():
            owned_file(self.root / name, value, config)
        # Runtime substitutions are path ABI only; mathematical execution stays
        # byte-for-byte as in the pinned A core.
        ml = (CORE_DIR / self.core_filename).read_text()
        ml = ml.replace('Path.explode "/worker"', 'Path.explode ' + ml_string(self.root))
        ml = ml.replace('"/candidate/"', ml_string(source_dir.as_posix() + '/'))
        owned_file(self.root / "server.ML", ml, config)
        names = list(request["sources"])
        qualifier = ml_string(config.get('source_qualifier', 'Generated_Refine'))
        def load_theory(name):
            return 'ignore (Thy_Info.use_theories (Options.default ()) ' + qualifier + ' [(' + ml_string(source_dir / name) + ', Position.none)]);'
        loads = []
        if request['gate']:
            if 'Freeform_Support' not in names:
                raise ValueError('native gate has no trusted support theory')
            names.remove('Freeform_Support')
            owned_file(self.root / 'gate.thy_fragment', request['gate'], config)
            loads.append(load_theory('Freeform_Support'))
            # Validate every generated source before loading any generated
            # import or executing a prefix. The support source is trusted.
            loads.append('let val support_name = #theory_name (Resources.import_name '
                + qualifier + ' (Path.explode ' + ml_string(source_dir) + ') "Freeform_Support"); '
                'val thy = Thy_Info.get_theory support_name; '
                'val initial = Toplevel.make_state (SOME (Theory.begin_theory '
                '("Backend_Gate", Position.none) [thy])); '
                'val transitions = Outer_Syntax.parse_text thy (K thy) Position.none '
                + '(File.read (Path.explode ' + ml_string(self.root / 'gate.thy_fragment') + ')); '
                'val _ = fold (Toplevel.command_exception true) transitions initial; '
                'in () end;')
        loads.extend(load_theory(name) for name in names)
        # Thy_Info's database uses session-qualified names (e.g. Draft.Foo).
        # Resolve these through Isabelle itself rather than assuming header names
        # equal loaded theory keys. Preserve the owner's ordinary cold-check name
        # so generated constants match helper-reference YXML identities.
        loads.append('let fun resolve name = #theory_name (Resources.import_name '
            + qualifier + ' (Path.explode ' + ml_string(source_dir) + ') name); '
            'val imports_path = Path.explode ' + ml_string(self.root / 'imports') + '; '
            'val _ = File.write imports_path (cat_lines (map resolve (split_lines (File.read imports_path)))); '
            'val _ = File.write (Path.explode ' + ml_string(self.root / 'theory_name') + ') '
            '(resolve ' + ml_string(self.label) + '); in () end;')
        launch = '\n'.join(loads) + '\nML_file ' + ml_string(self.root / "server.ML")
        owned_file(self.root / "bootstrap.ML", launch, config)
        bootstrap_call = '(ML_file ' + ml_string(self.root / "bootstrap.ML") + '; '
        bootstrap_call += 'OS.Process.exit OS.Process.success) handle exn => '
        bootstrap_call += '(writeln (Runtime.exn_message exn); OS.Process.exit OS.Process.failure)'
        self.command = process_command(config) + ["-e", bootstrap_call]
        env = os.environ.copy()
        env.update(config.get("env", {}))
        if time.monotonic() >= deadline:
            raise TimeoutError("global request deadline reached before worker launch")
        self.log = owned_file(self.root / "worker.log", "", config).open("w")
        launch = subprocess.Popen if process_registry is None else process_registry.popen
        registration = {} if process_registry is None else {"kind": "prover"}
        self.process = launch(self.command, stdout=self.log,
                                        stderr=subprocess.STDOUT, env=env,
                                        start_new_session=True, text=True, **registration)
        atexit.register(self.close)
        try:
            self.wait(self.root / 'ready')
            self.prefix_audited = audit_markers((self.root / 'worker.log').read_text())
        except Exception:
            self.close()
            raise

    def _request(self, body, mode):
        # A's core writes the control files and atomically renames .tmp to
        # .request. Pre-create checker-owned files before those writes so root's
        # restrictive umask cannot make a later warm/advance request unreadable.
        # Bootstrap also rewrites imports/theory_name, both owned at launch.
        stem = str(self.index + 1)
        for suffix in ('.mode', '.fact', '.tmp'):
            owned_file(self.root / (stem + suffix), '', self.checker.config)
        result = super()._request(body, mode)
        if mode == 'advance' and result['verdict'] == 'green':
            self.prefix_audited = list(dict.fromkeys([
                *self.prefix_audited,
                *audit_markers(Path(result['command_log']).read_text()),
            ]))
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        (self.root / "stop").touch()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            kill_tree(self.process.pid)
            self.process.wait(timeout=10)
        self.log.close()
        atexit.unregister(self.close)


class SharedNativeBackend:
    def __init__(self, config, cold_check):
        self.config, self.cold_check = dict(config), cold_check
        self.core_sha256 = core_identity()
        qualifier = config.get('source_qualifier', 'Generated_Refine')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', qualifier):
            raise ValueError('invalid shared source qualifier')
        expected = config.get("shared_core_sha256")
        if expected is not None and expected != self.core_sha256:
            raise ValueError("frozen shared Isabelle core identity mismatch")
        root = config.get("shared_backend_root")
        if not root or not Path(root).is_absolute():
            raise ValueError("shared backend requires an absolute run-private root")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.worker = None
        self.persistent_disabled = False
        # Controller-only callback, never an agent-supplied configuration value.
        self.resource_remaining_seconds = None
        self.process_registry = None

    def _resource_remaining(self):
        if self.resource_remaining_seconds is None:
            return float("inf")
        return max(0.0, float(self.resource_remaining_seconds()))

    def close(self):
        if self.worker is not None:
            self.worker.close()
        self.worker = None

    def _metadata(self):
        return {"backend": BACKEND, "shared_core_sha256": self.core_sha256,
                "command_timeout_seconds": self.config.get("command_timeout_seconds", 60),
                "source_qualifier": self.config.get("source_qualifier", "Generated_Refine")}

    def check(self, directory, owner, timeout=None):
        started_at, started = time.time(), time.monotonic()
        deadline = float('inf')
        if self.config.get("total_timeout_seconds") is not None:
            deadline = started + self.config["total_timeout_seconds"]
        deadline = min(deadline, time.monotonic() + self._resource_remaining())
        reason = None
        try:
            if timeout is not None:
                raise ValueError("explicit_timeout_full_check")
            if self.persistent_disabled:
                raise ValueError("persistent_disabled_after_native_error")
            request = source_request(directory, owner)
            worker = self.worker
            reused, advanced = False, False
            if worker is not None:
                worker.checker.request_deadline = deadline
                same_context = (worker.binding == request['closure_sha256']
                                and worker.label == owner
                                and worker.imports == tuple(request['imports']))
                if same_context:
                    if request['gate']:
                        gate_trial = worker.check(request['gate'] + '\n' + '\n'.join(
                            ['end'] * worker.prefix_depth))
                        if gate_trial['verdict'] != 'green':
                            gate_log = Path(gate_trial['command_log']).read_text()
                            if 'repository command gate:' not in gate_log + gate_trial['diagnostic']:
                                raise RuntimeError('native gate failed: ' + gate_trial['diagnostic'])
                            return {**self._metadata(), 'status': 'source_policy_violation',
                                    'returncode': 1, 'log': gate_log + '\n' + gate_trial['diagnostic'],
                                    'audited': [], 'elapsed_seconds': time.monotonic() - started,
                                    'started_at': started_at, 'finished_at': time.time(),
                                    'persistent': True, 'prefix_reused': True,
                                    'worker_id': worker.name, 'worker_evidence': str(worker.root),
                                    'failure_kind': 'unsupported_or_malformed_command'}
                    _, _, new_prefix, _ = prefix_parts(request['synthetic'], TARGET_ID, request['imports'])
                    # A advance requires a theory boundary. Within an open
                    # locale/context only identical prefixes can be reused.
                    can_advance = request['prefix_depth'] == 0 or new_prefix == worker.prefix
                    if can_advance:
                        advanced = new_prefix != worker.prefix
                        reused = worker.align(request['synthetic'], TARGET_ID, CERTIFICATION_FACT)
                        if reused:
                            worker.prefix_depth = request['prefix_depth']
                if not reused:
                    self.close()
            if self.worker is None:
                registration = {} if self.process_registry is None else {"process_registry": self.process_registry}
                self.worker = LocalWorker(self.config, request, self.root, deadline, **registration)
            prefix_seconds = time.monotonic() - started
            # The exact snapshot was gated before prefix/import execution;
            # the candidate contains the unchanged frontend audit instrumentation.
            candidate = request['candidate']
            frozen_source = self.worker.root / (str(self.worker.index + 1) + '.full.thy')
            frozen_source.write_text(request['source'])
            result = self.worker.check(candidate)
            log = Path(result['command_log']).read_text()
            status = 'ok' if result['verdict'] == 'green' else 'build_error'
            if 'Timeout.TIMEOUT' in result.get('diagnostic', '') or 'Timeout' in result.get('diagnostic', ''):
                status = 'timeout'
            failure_kind = None
            if 'Warning - Unable to increase stack - interrupting thread' in log:
                failure_kind, status = 'stack_exhausted', 'resource_exhausted'
            elif 'exception Interrupt_Breakdown raised' in log:
                failure_kind, status = 'native_interrupt', 'infrastructure_error'
            log += '\n' + result.get('diagnostic', '')
            current_audited = audit_markers(log)
            audited = list(dict.fromkeys([*getattr(self.worker, 'prefix_audited', []), *current_audited]))
            target_audited = request['target'] + ':' + request['polarity'] in current_audited
            if status == 'ok' and not target_audited:
                status = 'missing_audit'
            return {**self._metadata(), 'status': status, 'failure_kind': failure_kind,
                    'returncode': result.get('returncode'), 'log': log,
                    'elapsed_seconds': time.monotonic() - started,
                    'started_at': started_at, 'finished_at': time.time(),
                    'audited': audited, 'target_id': request['target'],
                    'target_audit_verified': target_audited,
                    'command': self.worker.command, 'worker_id': self.worker.name,
                    'persistent': True, 'prefix_reused': reused, 'prefix_advanced': advanced,
                    'prefix_prepare_seconds': prefix_seconds,
                    'dependency_loading_seconds': prefix_seconds,
                    'timing_semantics': 'prefix_prepare_includes_source_validation_gate_and_advance',
                    'owner_check_seconds': result['wall_ms'] / 1000,
                    'source_sha256': digest(request['source']),
                    'import_closure_sha256': request['closure_sha256'],
                    'command_timings': result.get('command_timings', []),
                    'worker_evidence': str(self.worker.root), 'frozen_source': str(frozen_source),
                    'timeout_phase': 'owner_theory' if status == 'timeout' else None,
                    'timeout_limit': 'command' if status == 'timeout' else None}
        except (ValueError, OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
            reason = str(error)
            if not isinstance(error, ValueError):
                # Match A: an exceptional worker failure disables acceleration
                # for this checker instance; ordinary RED candidates do not.
                self.persistent_disabled = True
            self.close()
        # A's current policy gives cold fallback its own complete-check timeout.
        # Any optional whole-call cap is stricter and shared explicitly via config.
        acceleration_seconds = time.monotonic() - started
        remaining = min(deadline - time.monotonic(), self._resource_remaining())
        if remaining <= 0:
            result = {'status': 'timeout', 'returncode': None, 'log': reason,
                      'audited': [], 'timeout_phase': 'persistent', 'timeout_limit': 'total_check'}
        else:
            budget = timeout if timeout is not None else self.config.get('timeout_seconds', 300)
            result = self.cold_check(directory, owner, timeout=min(budget, remaining))
        if 'repository command gate:' in result.get('log', ''):
            result = {**result, 'status': 'source_policy_violation',
                      'failure_kind': 'unsupported_or_malformed_command'}
        return {**result, **self._metadata(), 'persistent': False,
                'fallback_reason': reason, 'persistent_disabled': self.persistent_disabled,
                'persistent_elapsed_seconds': acceleration_seconds,
                'elapsed_seconds': time.monotonic() - started,
                'started_at': started_at, 'finished_at': time.time()}

    def build_session(self, directory, theories, session_name='Generated_Refine', timeout=None):
        if session_name != self.config.get('source_qualifier', 'Generated_Refine'):
            raise ValueError('build session must match the frozen source qualifier')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', session_name):
            raise ValueError('invalid generated session name')
        theories = list(theories)
        if not theories or len(set(theories)) != len(theories) or any(
                not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name) for name in theories):
            raise ValueError('build requires unique generated theory names')
        directory = Path(directory).resolve()
        build = owned_directory(self.root / ('build-' + uuid.uuid4().hex), self.config)
        sources = {}
        for path in directory.glob('*.thy'):
            if path.is_symlink():
                raise ValueError('build source must not be a symlink')
            sources[path.name] = path.read_text()
            owned_file(build / path.name, sources[path.name], self.config)
        if any(name + '.thy' not in sources for name in theories):
            raise ValueError('build target missing from source snapshot')
        # Match A's parent-session declarations, configurable for small HOL
        # controls. Real Refine runs use the same Lib/CorresK/AInvs set.
        parents = self.config.get('build_extra_sessions', ['Lib', 'CorresK', 'AInvs'])
        if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name) for name in parents):
            raise ValueError('invalid build parent session')
        root_text = '\n'.join([
            'session ' + session_name + ' = ' + self.config.get('session', 'Semantic_Base') + ' +',
            *(['  sessions', *('    ' + name for name in parents)] if parents else []),
            '  theories', *('    ' + name for name in theories), ''])
        owned_file(build / 'ROOT', root_text, self.config)
        command = list(self.config.get('command_prefix', [])) + [
            self.config.get('isabelle', '/workspace/verification/isabelle/bin/isabelle'),
            'build', '-b', '-o', 'threads=' + str(self.config.get('threads', 8)),
            '-o', 'system_heaps=false']
        for path in self.config.get('session_dirs', []):
            command += ['-d', str(path)]
        command += ['-d', str(build), session_name]
        env = os.environ.copy()
        env.update(self.config.get('env', {}))
        started_at, started = time.time(), time.monotonic()
        budget = timeout if timeout is not None else self.config.get('build_timeout_seconds', self.config.get('timeout_seconds', 300))
        budget = min(budget, self._resource_remaining())
        status, returncode = 'infrastructure_error', None
        try:
            if budget <= 0:
                raise subprocess.TimeoutExpired(command, 0)
            launch = subprocess.Popen if self.process_registry is None else self.process_registry.popen
            registration = {} if self.process_registry is None else {"kind": "build"}
            process = launch(command, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True,
                                       env=env, start_new_session=True, **registration)
            try:
                log, _ = process.communicate(timeout=budget)
                returncode = process.returncode
                status = 'ok' if returncode == 0 else 'build_error'
            except subprocess.TimeoutExpired:
                kill_tree(process.pid)
                log, _ = process.communicate()
                status = 'timeout'
        except subprocess.TimeoutExpired:
            status, log = "timeout", "global resource deadline reached before build launch"
        except OSError as error:
            log = str(error)
        owned_file(build / 'build.log', log, self.config)
        result = {**self._metadata(), 'status': status, 'returncode': returncode,
                  'log': log, 'elapsed_seconds': time.monotonic() - started,
                  'started_at': started_at, 'finished_at': time.time(),
                  'command': command, 'source_sha256': digest(sources),
                  'root_sha256': hashlib.sha256(root_text.encode()).hexdigest(),
                  'theories': theories, 'session': session_name,
                  'evidence': str(build), 'timeout_seconds': budget}
        owned_file(build / 'result.json', json.dumps({k:v for k,v in result.items() if k != 'log'}, indent=2) + '\n', self.config)
        return result
