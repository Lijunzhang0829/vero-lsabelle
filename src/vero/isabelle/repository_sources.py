"""Source constraints for independently budgeted repository proof nodes.

This is a conservative source profile, not a replacement Isabelle parser. Native
elaboration and proof audits remain authoritative. A node's local Isar facts are
part of its one candidate; separate theorem commands require registered IDs.
"""

import hashlib
import json
import re

from .freeform_sources import OUTER, tokens
from .freeform_sources import snapshot as freeform_snapshot

NODE_ID = re.compile(r"[ch][0-9]{4}\Z")
THEOREMS = {"lemma", "theorem", "corollary", "proposition"}
SEMANTIC_COMMANDS = {"definition", "abbreviation", "locale", "lemmas", "declare"}
# These commands can start independent proof obligations or install untracked
# declarations. Keep the experiment profile explicit rather than silently
# treating such obligations as part of a following registered theorem's check.
UNTRACKED_COMMANDS = {
    "schematic_goal",
    "notepad",
    "experiment",
    "sublocale",
    "instantiation",
    "instance",
    "typedef",
    "quotient_type",
    "lift_definition",
    "function",
    "termination",
    "fun",
    "primrec",
    "inductive",
    "coinductive",
    "inductive_set",
    "coinductive_set",
    "datatype",
    "codatatype",
    "record",
    "class",
    "subclass",
    "overloading",
    "consts",
    "typedecl",
    "type_synonym",
    "bundle",
    "include",
    "including",
    "unbundle",
    "hide_fact",
    "hide_const",
    "hide_type",
    "hide_class",
}


def _signature(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalized(text):
    return [(token.value, token.quoted) for token in tokens(text)]


def _metadata(module, known_ids):
    text = module["text"]
    ts = tokens(text)
    for token in ts:
        if not token.quoted and token.value in UNTRACKED_COMMANDS:
            raise ValueError("untracked repository command: " + token.value)
    starts = [
        index
        for index, token in enumerate(ts)
        if token.start >= module["body_start"]
        and not token.quoted
        and token.value in OUTER | {"interpretation"}
    ]
    contexts, node_contexts, semantics = [], {}, {}
    for number, start in enumerate(starts):
        stop = starts[number + 1] if number + 1 < len(starts) else len(ts)
        segment = ts[start:stop]
        command = segment[0].value
        end = ts[stop].start if stop < len(ts) else len(text)
        segment_text = text[segment[0].start : end]
        if command == "interpretation":
            # Sanitized Refine contracts inherit this exact locale context.
            # Allow no independently generated interpretation proof method.
            if not contexts or [(token.value, token.quoted) for token in segment] != [
                ("interpretation", False),
                ("Arch", False),
                (".", False),
            ]:
                raise ValueError("untracked repository command: interpretation")
            contexts[-1] = _signature(
                {"context": contexts[-1], "interpretation": "Arch ."}
            )
        if command in THEOREMS:
            name = segment[1].value if len(segment) > 1 else ""
            if not NODE_ID.fullmatch(name) or name not in known_ids:
                raise ValueError(
                    "every theorem needs a registered contract/helper ID: " + name
                )
            node_contexts[name] = list(contexts)
        if command in SEMANTIC_COMMANDS:
            binding = segment[1].value if len(segment) > 1 else ""
            # Definitions may omit a binding. Their full command identity still
            # makes deletion/replacement visible. 'declare' has no own binding.
            identity = (
                _signature(_normalized(segment_text))
                if command == "declare" or not binding or segment[1].quoted
                else binding
            )
            key = _signature(
                {"command": command, "binding": identity, "context": contexts}
            )
            value = _signature(_normalized(segment_text))
            if key in semantics and semantics[key] != value:
                raise ValueError("conflicting semantic declarations: " + binding)
            semantics[key] = value
        if command in {"context", "locale"}:
            begins = [
                token
                for token in segment
                if not token.quoted and token.value == "begin"
            ]
            if begins:
                header = text[segment[0].start : begins[0].end]
                contexts.append(_signature(_normalized(header)))
        elif command == "end" and contexts:
            contexts.pop()
    return node_contexts, semantics


def snapshot(workspace, known_ids, base_import, files=None):
    """Freeze ordinary sources and reject independently unmetered proofs."""
    known_ids = set(known_ids)
    if any(not isinstance(key, str) or not NODE_ID.fullmatch(key) for key in known_ids):
        raise ValueError("registered IDs must use cNNNN or hNNNN")
    frozen = freeform_snapshot(workspace, known_ids, base_import, files)
    node_contexts, semantics = {}, {}
    for module in frozen["modules"].values():
        current_contexts, current_semantics = _metadata(module, known_ids)
        node_contexts.update(current_contexts)
        for key, value in current_semantics.items():
            if key in semantics and semantics[key] != value:
                raise ValueError("conflicting semantic declarations across theories")
            semantics[key] = value
    if set(node_contexts) != set(frozen["contracts"]):
        raise ValueError("repository node extraction mismatch")
    frozen["node_contexts"] = node_contexts
    frozen["semantic_signatures"] = semantics
    return frozen


def _source(frozen, node_id):
    record = frozen["contracts"][node_id]
    return record, frozen["modules"][record["module"]]["text"]


def declaration(frozen, node_id):
    record, text = _source(frozen, node_id)
    return text[record["start"] : record["end"]]


def statement_text(frozen, node_id):
    record, text = _source(frozen, node_id)
    return text[record["start"] : record["proof_start"]]


def proof_text(frozen, node_id):
    record, text = _source(frozen, node_id)
    return text[record["proof_start"] : record["end"]]


def node_signature(frozen, node_id):
    return _signature(
        {
            "statement": _normalized(statement_text(frozen, node_id)),
            "proof": _normalized(proof_text(frozen, node_id)),
            "context": frozen["node_contexts"][node_id],
        }
    )


def declaration_context_signature(frozen, node_id, accepted):
    """Identity of a statement's legal prefix and transitive generated imports.

    Pending proof bodies never enter the context. Accepted preceding proofs and
    complete imported sources do, since they can publish facts/attributes used by
    later declarations. The controller separately requires every generated import
    to be sealed before using this certificate. The parent session, reference
    terms and checker implementation are immutable controller bindings.
    """
    record = frozen["contracts"][node_id]
    owner = record["module"]
    accepted = set(accepted)

    def legal_text(module, stop):
        text = module["text"][:stop]
        for declaration in reversed(module["contracts"]):
            if declaration["end"] <= stop and declaration["id"] not in accepted:
                text = (
                    text[: declaration["proof_start"]]
                    + "oops\n"
                    + text[declaration["end"] :]
                )
        return _normalized(text)

    closure = set()

    def visit(name):
        for dependency in frozen["modules"][name]["dependencies"]:
            if dependency not in closure:
                closure.add(dependency)
                visit(dependency)

    visit(owner)
    return _signature(
        {
            "schema": "legal-declaration-context-v1",
            "owner": owner,
            "prefix": legal_text(frozen["modules"][owner], record["proof_start"]),
            "imports": {
                name: legal_text(module, len(module["text"]))
                for name in sorted(closure)
                for module in [frozen["modules"][name]]
            },
        }
    )


def validate_accepted(frozen, accepted_snapshots):
    """Reject edited/deleted GREEN nodes, independent of theory grouping.

    The mapping is owned by the supervisor: each key maps to the actual frozen
    snapshot from that node's successful check, never a model-provided digest.
    """
    for node_id, accepted in accepted_snapshots.items():
        if node_id not in frozen["contracts"]:
            raise ValueError("accepted node removed: " + node_id)
        if node_signature(frozen, node_id) != node_signature(accepted, node_id):
            raise ValueError("accepted statement, proof or context changed: " + node_id)


def semantic_signatures(frozen):
    """Grouping-independent identity of supported nonproof declarations."""
    return dict(frozen["semantic_signatures"])


def validate_semantics(previous, current):
    """Keep previously frozen declarations; permit additional declarations.

    Arguments are signature mappings returned by semantic_signatures. This detects
    text/context changes, not logical equivalence. Native checks still establish
    that additions and relocation preserve the accepted theorems' meaning.
    """
    for key, value in previous.items():
        if current.get(key) != value:
            raise ValueError("frozen semantic declaration changed or removed: " + key)
