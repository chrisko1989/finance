"""Atomic local persistence with optimistic concurrency control."""
import copy
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .core import DEFAULT_CONFIG, ValidationError


class ConflictError(ValidationError):
    pass


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "portfolio.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS portfolio (id INTEGER PRIMARY KEY CHECK (id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL)")
            empty = {"schema_version": 2, "config": copy.deepcopy(DEFAULT_CONFIG), "holdings": [], "history": [], "demo": False}
            db.execute("INSERT OR IGNORE INTO portfolio VALUES (1, 0, ?)", (json.dumps(empty),))
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def normalize(state):
        from .ledger import normalize_state
        return normalize_state(state)

    def read(self):
        with self.connect() as db:
            revision, payload = db.execute("SELECT revision, payload FROM portfolio WHERE id=1").fetchone()
        state = self.normalize(json.loads(payload))
        state["revision"] = revision
        return state

    def change(self, expected_revision, mutate):
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValidationError("Versionsnummer fehlt. Bitte die Ansicht neu laden.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision, payload = db.execute("SELECT revision, payload FROM portfolio WHERE id=1").fetchone()
            if revision != expected_revision:
                raise ConflictError("Das Depot wurde inzwischen geändert. Bitte neu laden und den Import erneut prüfen.")
            state = self.normalize(json.loads(payload))
            mutate(state)
            db.execute("UPDATE portfolio SET revision=?, payload=? WHERE id=1", (revision + 1, json.dumps(state, ensure_ascii=False, allow_nan=False)))
        return self.read()

    def backup(self, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            raise ValidationError("Sicherungsdatei existiert bereits.")
        with self.connect() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
        os.chmod(destination, 0o600)
