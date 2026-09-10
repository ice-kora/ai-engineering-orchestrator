"""P1 adapters: thin, typed wrappers around the P0-verified component CLIs.

Design rules (from P0 real-run evidence, see docs/P0-REPORT-ROUND2.md):
- bd: run with process cwd inside the target repo (`-C` refuses dirs without .beads);
  task ids look like ``<repo-prefix>-<rand4>``; labels via ``update --add-label``
  or single-label ``label add``; close message is ``--reason``.
- am: strict clap ordering (options BEFORE positionals); ``file_reservations
  reserve`` has no --json flag and prints JSON by default; a conflicting reserve
  STILL EXITS 0 — success is decided by parsing {granted, conflicts}, never by
  exit code; agent names must be auto-generated adjective+noun (persisted).
- agy: pass absolute working directories (its git toplevel inference inside
  worktrees is unreliable — antigravity-cli#68) and verify file placement after
  runs.
"""

from adapters.config import PATHS  # re-export convenience
