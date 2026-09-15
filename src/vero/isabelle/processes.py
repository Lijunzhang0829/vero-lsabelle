"""Terminate Linux prover descendants, including Isabelle's separate process groups."""

import os
import signal
from pathlib import Path


def kill_tree(pid):
    # Stop each parent before enumerating children: no new descendants can escape
    # between enumeration and termination. Never signal unrelated process groups.
    try:
        os.kill(pid, signal.SIGSTOP)
    except ProcessLookupError:
        return
    children = []
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
            if int(fields[1]) == pid:
                children.append(int(stat.parent.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
    for child in children:
        kill_tree(child)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
