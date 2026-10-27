"""Read ordinary theory files; no proof slots or predetermined theory grouping."""

import re
from dataclasses import dataclass
from pathlib import Path

# Outer command boundaries. Unsupported commands fail closed before native loading.
OUTER = set("theory context locale lemma theorem corollary proposition definition abbreviation lemmas declare end text text_raw section subsection subsubsection paragraph subparagraph chapter notepad thm term typ value find_theorems print_statement print_theorems print_context print_locale print_facts".split())
UNSAFE = re.compile(r"\b(?:ML\w*|SML\w*|setup|local_setup|method_setup|attribute_setup|simproc_setup|tactic|oracle|axiomatization|axioms|sorry|use|external_file|keywords|syntax|translations|parse_translation|print_translation|code_printing|export_code|export_generated_files|generate_file|compile_generated_files|contract_reference|Freeform_Reference)\b")
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
TARGET = re.compile(r"[ch][0-9]{4}\Z")
PROOF_START = {"by", "proof", "apply", "using", "unfolding", "oops"}


class ContextError(ValueError):
    """Invalid dependency context, which VERO does not charge as a proof attempt."""


@dataclass
class Token:
    value: str
    start: int
    end: int
    quoted: bool = False


def tokens(text):
    result, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        start = i
        if text.startswith("(*", i):
            depth = 1
            i += 2
            while depth and i < len(text):
                if text.startswith("(*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*)", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth:
                raise ValueError("unclosed comment")
            continue
        if text[i] == '"':
            i += 1
            value = []
            while i < len(text) and text[i] != '"':
                if text[i] == '\\' and i + 1 < len(text) and text[i+1] in {'"', '\\'}:
                    value.append(text[i+1])
                    i += 2
                else:
                    value.append(text[i])
                    i += 1
            if i == len(text):
                raise ValueError("unclosed string")
            i += 1
            result.append(Token(''.join(value), start, i, True))
            continue
        opener = next((s for s in ('\\<open>', '‹') if text.startswith(s, i)), None)
        if opener:
            depth = 1
            i += len(opener)
            while depth and i < len(text):
                opening = next((s for s in ('\\<open>', '‹') if text.startswith(s, i)), None)
                closing = next((s for s in ('\\<close>', '›') if text.startswith(s, i)), None)
                if opening:
                    depth += 1
                    i += len(opening)
                elif closing:
                    depth -= 1
                    i += len(closing)
                else:
                    i += 1
            if depth:
                raise ValueError("unclosed cartouche")
            result.append(Token(text[start:i], start, i, True))
            continue
        match = re.match(r"[A-Za-z0-9_'.]+", text[i:])
        i += len(match[0]) if match else 1
        result.append(Token(text[start:i], start, i))
    return result


def parse(text, path, known_ids):
    ts = tokens(text)
    # Text and strings cannot introduce executable commands, but nested method
    # cartouches can. Conservative scanning retains the old checker's ML boundary.
    clean = '\n'.join(t.value for t in ts)
    bad = UNSAFE.search(clean)
    if bad:
        raise ValueError("unsupported executable/axiom token: " + bad[0])
    if len(ts) < 5 or ts[0].value != 'theory' or ts[2].value != 'imports':
        raise ValueError("expected theory NAME imports ... begin")
    name = ts[1].value
    if not IDENT.fullmatch(name) or name in {'Freeform_Support', 'Freeform_Reference', 'Freeform_Integration'}:
        raise ValueError("invalid/reserved theory name")
    if Path(path).stem != name:
        raise ValueError("theory name must match filename")
    begin = next((i for i in range(3, len(ts)) if not ts[i].quoted and ts[i].value == 'begin'), None)
    if begin is None:
        raise ValueError("missing theory begin")
    imports = [t.value for t in ts[3:begin]]
    starts = [i for i in range(begin+1, len(ts)) if not ts[i].quoted and ts[i].value in OUTER]
    declarations = []
    depth = 1
    for n, index in enumerate(starts):
        stop = starts[n+1] if n+1 < len(starts) else len(ts)
        command = ts[index].value
        end = ts[stop].start if stop < len(ts) else len(text)
        segment = ts[index:stop]
        if command in {'lemma', 'theorem', 'corollary', 'proposition'}:
            binding = segment[1].value if len(segment) > 1 else ''
            if TARGET.fullmatch(binding):
                if binding not in known_ids:
                    raise ValueError("unknown contract " + binding)
                proof = next((t for t in segment[2:] if not t.quoted and t.value in PROOF_START), None)
                if proof is None:
                    raise ValueError("missing proof for " + binding)
                declarations.append({'id': binding, 'start': ts[index].start,
                                     'proof_start': proof.start, 'end': end, 'depth': depth,
                                     'line': text.count('\n', 0, ts[index].start)+1})
        for t in segment:
            if not t.quoted and t.value == 'begin':
                depth += 1
            elif not t.quoted and t.value == 'end':
                depth -= 1
        if depth < 0:
            raise ValueError("unbalanced theory/context ends")
    if depth != 0:
        raise ValueError("theory/context ends are incomplete")
    return {'name': name, 'path': path, 'text': text, 'imports': imports,
            'body_start': ts[begin].end, 'contracts': declarations}


def snapshot(workspace, known_ids, base_import, files=None):
    root = Path(workspace).resolve()
    modules, paths, ids = {}, {}, {}
    if files is None:
        files = {}
        for path in sorted(root.rglob('*.thy')):
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
                raise ValueError("symlink theory is not allowed")
            if path.stat().st_size > 4_000_000:
                raise ValueError("theory source exceeds 4 MB")
            files[path.relative_to(root).as_posix()] = path.read_text()
    if not isinstance(files, dict):
        raise ValueError("source snapshot must map relative paths to text")
    for rel, text in sorted(files.items()):
        if not isinstance(rel, str) or not isinstance(text, str):
            raise ValueError("source paths and bodies must be strings")
        path = Path(rel)
        if path.is_absolute() or '..' in path.parts or path.suffix != '.thy':
            raise ValueError("source snapshot path outside workspace")
        if len(text.encode()) > 4_000_000:
            raise ValueError("theory source exceeds 4 MB")
        module = parse(text, rel, known_ids)
        name = module['name']
        if name in modules:
            raise ValueError("duplicate theory name " + name)
        modules[name], paths[rel[:-4]] = module, name
        for decl in module['contracts']:
            if decl['id'] in ids:
                raise ValueError("duplicate contract declaration " + decl['id'])
            ids[decl['id']] = {'module': name, **decl}
    for name, module in modules.items():
        deps = []
        for imp in module['imports']:
            if imp == base_import:
                continue
            relative = (Path(module['path']).parent / imp).as_posix()
            # Resolve syntactic .. against this virtual snapshot, never host files.
            import posixpath
            relative = posixpath.normpath(relative)
            dep = paths.get(relative) or (imp if imp in modules else None)
            if dep is None:
                raise ContextError("import outside parent/current run: " + imp)
            deps.append(dep)
        module['dependencies'] = list(dict.fromkeys(deps))
    order = []
    while len(order) < len(modules):
        ready = [n for n, m in modules.items() if n not in order and all(d in order for d in m['dependencies'])]
        if not ready:
            raise ContextError("theory import cycle")
        order.extend(ready)
    return {'modules': modules, 'contracts': ids, 'order': order}


def render(snapshot, destination, accepted, target=None):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    ranges = {}
    for name, module in snapshot['modules'].items():
        text = module['text']
        body_start = module['body_start']
        header = 'theory ' + name + '\nimports Freeform_Support ' + ' '.join(module['dependencies']) + '\nbegin\n'
        header += 'ML \\<open>writeln "VERO_THEORY_ENTER ' + name + '"\\<close>\n'
        edits = []
        cut, depth = len(text), 0
        for decl in module['contracts']:
            cid = decl['id']
            if cid == target:
                edits.append((decl['start'], decl['start'], '\nML \\<open>writeln "FREEFORM_TARGET_BEGIN ' + cid + '"\\<close>\n'))
            if cid == target or cid in accepted:
                audit = '\nlocal_setup \\<open>Freeform_Reference.audit "' + cid + '" "' + cid + '"\\<close>\n'
                edits.append((decl['end'], decl['end'], audit))
            else:
                edits.append((decl['proof_start'], decl['end'], 'oops\n'))
            if cid == target:
                cut, depth = decl['end'], decl['depth']
                break
        body = text[body_start:cut]
        for start, end, replacement in reversed(edits):
            body = body[:start-body_start] + replacement + body[end-body_start:]
        if depth:
            body += '\n' + '\n'.join(['end'] * depth) + '\n'
        output = header + body
        (destination / (name + '.thy')).write_text(output)
        ranges[name] = output
    return ranges
