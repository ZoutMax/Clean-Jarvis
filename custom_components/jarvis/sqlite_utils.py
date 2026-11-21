"""SQLite helpers with deterministic connection cleanup."""
from __future__ import annotations

import sqlite3


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite3, then close on context exit."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()
