"""Isabelle port of upstream imports/global_aux/proof_aux slot semantics.

Helpers are ordinary file declarations, not independently scheduled proof nodes.
"""

import copy
import re

from .benchmark import digest, validate


def enabled(bench):
    return bench.get("schema_version", 1) >= 2


def upgrade(bench):
    validate(bench)
    result = copy.deepcopy(bench)
    result.pop("benchmark_id")
    result["schema_version"] = 2
    result["policy"]["cross_module_proof_imports"] = True
    result["policy"]["auxiliary_slots"] = True
    result["policy"]["editable_generated_imports"] = True
    result.setdefault("provenance", {})["slot_upgrade_source"] = bench["benchmark_id"]
    return {**result, "benchmark_id": digest(result)}


def schedule(bench):
    """key -> (owner module, kind); same schedule drives rendering and extraction."""
    result = {}
    for c in bench["contracts"]:
        m = c["module"]
        if enabled(bench):
            result[m + ":imports"] = (m, "imports")
            result[m + ":global_aux"] = (m, "global_aux")
        for polarity in ("prove", "disprove"):
            result[c["id"] + ":" + polarity] = (m, "proof")
            if enabled(bench):
                result[c["id"] + ":" + polarity + "_aux"] = (m, "proof_aux")
    return result


def block(key, body):
    owner, kind = key.split(":")
    return f"(* VERO START {owner} {kind} *)\n\n{body.strip()}\n\n(* VERO END {owner} {kind} *)"


def import_names(body):
    from .checker import strip_comments

    text = strip_comments(body).strip()
    names = text.split()
    if any(not re.fullmatch(r"Vero_[A-Za-z0-9_]+", n) for n in names):
        raise ValueError("imports slot accepts generated theory names only")
    if len(names) != len(set(names)):
        raise ValueError("duplicate additional imports")
    return names


def context(bench, bodies):
    """Validated effective DAG; base PLAN edges are immutable, extra imports additive."""
    result = copy.deepcopy(bench)
    modules = sorted({c["module"] for c in bench["contracts"]})
    priority = bench.get("planning", {}).get("execution_order", modules)
    imports = copy.deepcopy(bench.get("planning", {}).get("imports", {}))
    for m in modules:
        added = import_names(bodies.get(m + ":imports", "")) if enabled(bench) else []
        if any(n not in modules or n == m for n in added):
            raise ValueError("imports slot references unknown/self theory")
        imports[m] = list(dict.fromkeys([*imports.get(m, []), *added]))
    order = []
    while len(order) < len(modules):
        ready = [
            m
            for m in priority
            if m not in order and all(d in order for d in imports[m])
        ]
        if not ready:
            raise ValueError("generated imports contain a cycle")
        order.extend(ready)
    result["planning"] = {
        **result.get("planning", {}),
        "imports": imports,
        "execution_order": order,
    }
    result.pop("benchmark_id")
    return {**result, "benchmark_id": digest(result)}


def auxiliary_hygiene(body):
    """Conservative native-language boundary; helpers cannot alter frozen context.

    Fresh reserved bindings prevent a helper from shadowing a constant in a fixed
    contract. No new context/locale, syntax or executable ML commands are accepted.
    """
    from .checker import FORBIDDEN, strip_comments

    try:
        text = strip_comments(body)
    except ValueError as exc:
        return str(exc)
    if not text.strip():
        return None
    for token in FORBIDDEN.finditer(text):
        if token[0] not in {
            "lemma",
            "theorem",
            "corollary",
            "proposition",
            "definition",
        }:
            return "forbidden auxiliary token: " + token[0]
    if re.search(
        r"\b(?:oops|consts|axiomatization|hide_const|hide_fact|hide_type|syntax_declaration|translations|notepad|experiment|typedecl|print_translation|parse_translation|term_check|term_uncheck|untyped_ast_translation|typed_print_translation|vero_prove|vero_disprove)\b",
        text,
    ):
        return "auxiliary slot contains a forbidden command or unfinished proof"
    # Ignore quoted proposition/definition text when checking declaration bindings.
    outer = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    declarations = list(
        re.finditer(
            r"\b(lemma|theorem|corollary|proposition|definition)\s+(\S+)", outer
        )
    )
    if not declarations or outer[: declarations[0].start()].strip():
        return "auxiliary slot must begin with a named lemma/theorem/definition"
    for match in declarations:
        name = match[2].rstrip(":")
        if not re.fullmatch(r"vero_aux_[A-Za-z0-9_]+", name):
            return "auxiliary declarations require fresh vero_aux_ names"
    return None


def slot_hygiene(kind, body):
    from .checker import hygiene

    if kind == "imports":
        try:
            import_names(body)
        except ValueError as exc:
            return str(exc)
        return None
    return auxiliary_hygiene(body) if kind != "proof" else hygiene(body)
