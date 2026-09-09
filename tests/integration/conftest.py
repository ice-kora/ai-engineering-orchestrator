"""Inject D-drive component install dirs into PATH for this pytest process.

Rationale: the ZCode host process inherited PATH before the User PATH update,
so freshly installed components are invisible to `shutil.which` unless we
prepend them per-process. Order matters: conftest runs before test module
imports (module-level `shutil.which` calls in _mail.py).
"""

import os
from pathlib import Path

_COMPONENT_DIRS = [
    r"D:\Software\ai-orchestrator\beads",
    r"D:\Software\ai-orchestrator\agent-mail",
]

# agy lives on C: per approved exception (official installer has no custom dir)
_agy = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin"
if _agy.is_dir():
    _COMPONENT_DIRS.append(str(_agy))

_existing = os.environ.get("PATH", "")
_added = [d for d in _COMPONENT_DIRS if Path(d).is_dir() and d not in _existing]
if _added:
    os.environ["PATH"] = ";".join(_added) + ";" + _existing
