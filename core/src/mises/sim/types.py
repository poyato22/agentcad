"""Small shared types for the sim pipeline."""
from __future__ import annotations


class SimError(Exception):
    """A study that could not be run or finished. `code` is machine-readable
    ("solver_missing", "timeout", "mesh_failed", "solve_failed", "bad_setup")."""

    def __init__(self, code: str, message: str, *, hint: str | None = None,
                 data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.data = data or {}
