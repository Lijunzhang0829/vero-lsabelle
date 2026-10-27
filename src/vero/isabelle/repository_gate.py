"""Native command-span gate installed inside the already budgeted Isabelle check.

The Python source profile rejects executable ML and unregistered theorem names.
This complementary native gate resolves the parent's actual command keywords,
including library extensions, before any generated theory executes. The small
ML/setup allowance covers only instrumentation inserted by the trusted renderer.
"""

import re
from pathlib import Path

# Isar proof commands execute inside the one charged node candidate. New outer
# proof-generation commands must never be accepted implicitly through imports.
ALLOWED_COMMANDS = frozenset(
    [
        "theory",
        "context",
        "locale",
        "interpretation",
        "lemma",
        "theorem",
        "corollary",
        "proposition",
        "definition",
        "abbreviation",
        "lemmas",
        "declare",
        "end",
        "text",
        "txt",
        "text_raw",
        "section",
        "subsection",
        "subsubsection",
        "paragraph",
        "subparagraph",
        "chapter",
        "thm",
        "term",
        "typ",
        "value",
        "find_theorems",
        "print_statement",
        "print_theorems",
        "print_context",
        "print_locale",
        "print_facts",
        "print_state",
        "by",
        "proof",
        "qed",
        "done",
        "oops",
        "apply",
        "apply_end",
        "defer",
        "prefer",
        "back",
        "subgoal",
        "have",
        "hence",
        "show",
        "thus",
        "then",
        "from",
        "with",
        "note",
        "supply",
        "using",
        "unfolding",
        "fix",
        "assume",
        "presume",
        "define",
        "consider",
        "obtain",
        "let",
        "write",
        "case",
        "next",
        "also",
        "moreover",
        "finally",
        "ultimately",
        "interpret",
        "include",
        "including",
        ".",
        "..",
        "{",
        "}",
        "ML",
        "local_setup",
        "contract_reference",
    ]
)
BEGIN = "(* VERO_REPOSITORY_COMMAND_GATE_BEGIN *)"
END = "(* VERO_REPOSITORY_COMMAND_GATE_END *)"


def _ml_string(value):
    """Encode UTF-8 bytes as a Standard ML string, without source interpolation."""
    pieces = []
    for byte in str(value).encode("utf-8"):
        if byte in (34, 92):
            pieces.append("\\" + chr(byte))
        elif 32 <= byte <= 126:
            pieces.append(chr(byte))
        else:
            pieces.append("\\" + str(byte).zfill(3))
    return '"' + "".join(pieces) + '"'


def install_gate(source_directory):
    """Append a trusted native validator before Freeform_Support's final end.

    Only call this on supervisor-rendered immutable source directories. Installing
    it does not start Isabelle, validate a proof, or consume a separate check.
    Reinstallation regenerates the file list, so newly rendered files cannot escape
    validation through an earlier installation.
    """
    source_directory = Path(source_directory).resolve()
    support = source_directory / "Freeform_Support.thy"
    text = support.read_text()
    if BEGIN in text:
        pattern = re.escape(BEGIN) + r".*?" + re.escape(END) + r"\s*"
        text, count = re.subn(pattern, "", text, flags=re.DOTALL)
        if count != 1:
            raise ValueError("ambiguous native command-gate installation")
    match = re.search(r"\bend\s*\Z", text)
    if match is None:
        raise ValueError("Freeform_Support has no final theory end")
    paths = sorted(p for p in source_directory.glob("*.thy") if p != support)
    if any(path.is_symlink() for path in paths):
        raise ValueError("rendered sources must not be symlinks")
    ml_paths = ", ".join(_ml_string(path) for path in paths)
    ml_allowed = ", ".join(_ml_string(command) for command in sorted(ALLOWED_COMMANDS))
    gate = (
        BEGIN + "\nML \\<open>\nlocal\n"
        "  val thy = \\<^theory>;\n"
        "  val allowed = [" + ml_allowed + "];\n"
        "  fun check_file file =\n"
        "    let\n"
        "      val transitions = Outer_Syntax.parse_text thy (K thy) Position.none\n"
        "        (File.read (Path.explode file));\n"
        "      fun check transition =\n"
        "        if Toplevel.is_ignored transition then ()\n"
        "        else if Toplevel.is_malformed transition then\n"
        '          error ("repository command gate: malformed command in " ^ file)\n'
        "        else if member (op =) allowed (Toplevel.name_of transition) then ()\n"
        '        else error ("repository command gate: unsupported command " ^\n'
        '          quote (Toplevel.name_of transition) ^ " in " ^ file);\n'
        "    in List.app check transitions end;\n"
        "in\n"
        "  val _ = List.app check_file [" + ml_paths + "];\n"
        '  val _ = writeln "VERO_REPOSITORY_COMMAND_GATE clean";\n'
        "end;\n\\<close>\n" + END + "\n"
    )
    support.write_text(text[: match.start()] + gate + text[match.start() :])
    return [str(path) for path in paths]
