"""Durable state in SQLite: the account register, people and roles, human tasks,
workflow steps, audit findings, and a run lease.

Why SQLite: one file, transactional, survives a crash mid-run. Every write that
matters is idempotent on a natural key, so re-running anything never duplicates.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .core import iso, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  brand TEXT PRIMARY KEY,
  handle TEXT, handle_candidates TEXT, owner TEXT,
  recovery_email TEXT, recovery_phone TEXT,
  two_factor_method TEXT, vault_refs TEXT DEFAULT '{}',
  status TEXT DEFAULT 'planned', blockers TEXT DEFAULT '[]',
  profile TEXT DEFAULT '{}', ig_user_id TEXT,
  last_verified_at TEXT, last_verified_how TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS people (
  email TEXT PRIMARY KEY, name TEXT, status TEXT DEFAULT 'active', is_test_member INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS roles (
  brand TEXT, person TEXT, surface TEXT, role TEXT,
  state TEXT DEFAULT 'requested',          -- requested | granted | removal_pending | removed
  verified_how TEXT DEFAULT 'unverified', last_confirmed_at TEXT,
  PRIMARY KEY (brand, person, surface)
);
CREATE TABLE IF NOT EXISTS vault_access (
  ref TEXT, person TEXT, PRIMARY KEY (ref, person)
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE,                          -- idempotency key: one task per real-world need
  brand TEXT, kind TEXT, title TEXT, instructions TEXT,
  owner TEXT, escalate_to TEXT, due_at TEXT,
  status TEXT DEFAULT 'open',              -- open | done | cancelled
  evidence TEXT DEFAULT '{}', reminders_sent INTEGER DEFAULT 0, escalated INTEGER DEFAULT 0,
  created_at TEXT, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS steps (
  brand TEXT, name TEXT, status TEXT,      -- pending | waiting_human | done | failed | blocked
  attempts INTEGER DEFAULT 0, detail TEXT, updated_at TEXT,
  PRIMARY KEY (brand, name)
);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, audit_id TEXT, brand TEXT, check_name TEXT,
  severity TEXT, detail TEXT, verification TEXT, task_key TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS leases (
  name TEXT PRIMARY KEY, holder TEXT, expires_at TEXT
);
"""


def new_holder() -> str:
    import os
    import socket
    import uuid

    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _alive(holder: str) -> bool:
    import os
    import socket

    parts = holder.split(":")
    if len(parts) != 3 or parts[0] != socket.gethostname() or not parts[1].isdigit():
        return True  # another host or an old-style holder: trust the expiry
    try:
        os.kill(int(parts[1]), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


ACCOUNT_COLUMNS = frozenset(
    {
        "handle",
        "handle_candidates",
        "owner",
        "recovery_email",
        "recovery_phone",
        "two_factor_method",
        "vault_refs",
        "status",
        "blockers",
        "profile",
        "ig_user_id",
        "last_verified_at",
        "last_verified_how",
        "created_at",
        "updated_at",
    }
)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        try:  # migration: keep revoked grants for the record instead of deleting them
            self.db.execute("ALTER TABLE vault_access ADD COLUMN revoked_at TEXT")
        except sqlite3.OperationalError:
            pass

    @contextmanager
    def tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # ---------------------------------------------------------- lease
    def acquire(self, name: str, holder: str, minutes: int = 15) -> bool:
        """One runner per workflow at a time, so two runs can never race into duplicates.
        Holders look like host:pid:nonce. A lease held by a process that no longer exists on
        this host is taken over at once, so recovery after a kill is immediate, not 15 minutes later."""
        with self.tx() as db:
            row = db.execute("SELECT holder, expires_at FROM leases WHERE name=?", (name,)).fetchone()
            if (
                row
                and row["holder"] != holder
                and datetime.fromisoformat(row["expires_at"]) > now()
                and _alive(row["holder"])
            ):
                return False
            db.execute(
                "INSERT OR REPLACE INTO leases VALUES (?,?,?)", (name, holder, iso(now() + timedelta(minutes=minutes)))
            )
            return True

    def release(self, name: str, holder: str) -> None:
        self.db.execute("DELETE FROM leases WHERE name=? AND holder=?", (name, holder))

    # ---------------------------------------------------------- accounts
    def account(self, brand: str) -> dict | None:
        row = self.db.execute("SELECT * FROM accounts WHERE brand=?", (brand,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("vault_refs", "blockers", "profile", "handle_candidates"):
            d[k] = json.loads(d[k]) if d[k] else ([] if k in ("blockers", "handle_candidates") else {})
        return d

    def upsert_account(self, brand: str, **fields) -> dict:
        unknown = set(fields) - ACCOUNT_COLUMNS
        if unknown:
            raise ValueError(f"unknown account fields: {sorted(unknown)}")
        cur = self.account(brand)
        for k in ("vault_refs", "blockers", "profile", "handle_candidates"):
            if k in fields:
                fields[k] = json.dumps(fields[k])
        fields["updated_at"] = iso()
        if cur is None:
            fields.setdefault("created_at", iso())
            cols = ["brand", *fields]
            # Column names come only from ACCOUNT_COLUMNS (checked above); values are bound parameters.
            self.db.execute(
                f"INSERT INTO accounts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",  # noqa: S608
                [brand, *fields.values()],
            )
        elif fields:
            sets = ",".join(f"{k}=?" for k in fields)
            self.db.execute(f"UPDATE accounts SET {sets} WHERE brand=?", [*fields.values(), brand])  # noqa: S608
        return self.require_account(brand)

    def require_account(self, brand: str) -> dict:
        acc = self.account(brand)
        if acc is None:
            raise KeyError(f"no account '{brand}' in the register")
        return acc

    def accounts(self) -> list[dict]:
        return [self.require_account(r["brand"]) for r in self.db.execute("SELECT brand FROM accounts ORDER BY brand")]

    def set_blocker(self, brand: str, key: str, text: str | None) -> None:
        acc = self.account(brand) or {"blockers": []}
        blockers = [b for b in acc["blockers"] if b["key"] != key]
        if text:
            blockers.append({"key": key, "text": text, "since": iso()})
        self.upsert_account(brand, blockers=blockers)

    # ---------------------------------------------------------- people and roles
    def upsert_person(self, email: str, name: str, status: str = "active", is_test_member: bool = False):
        self.db.execute(
            "INSERT INTO people VALUES (?,?,?,?) ON CONFLICT(email) DO UPDATE SET name=excluded.name,"
            " is_test_member=excluded.is_test_member",
            (email, name, status, int(is_test_member)),
        )

    def set_person_status(self, email: str, status: str):
        self.db.execute("UPDATE people SET status=? WHERE email=?", (status, email))

    def people(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM people ORDER BY email")]

    def request_role(self, brand, person, surface, role):
        self.db.execute(
            "INSERT INTO roles (brand, person, surface, role) VALUES (?,?,?,?) "
            "ON CONFLICT(brand, person, surface) DO UPDATE SET role=excluded.role "
            "WHERE roles.state IN ('requested','granted')",
            (brand, person, surface, role),
        )

    def set_role_state(self, brand, person, surface, state, verified_how=None):
        self.db.execute(
            "UPDATE roles SET state=?, verified_how=COALESCE(?, verified_how), last_confirmed_at=? "
            "WHERE brand=? AND person=? AND surface=?",
            (state, verified_how, iso(), brand, person, surface),
        )

    def roles(self, brand: str | None = None, person: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM roles WHERE 1=1", []
        if brand:
            q += " AND brand=?"
            args.append(brand)
        if person:
            q += " AND person=?"
            args.append(person)
        return [dict(r) for r in self.db.execute(q + " ORDER BY brand, person, surface", args)]

    def grant_vault_access(self, ref: str, person: str):
        self.db.execute("INSERT OR IGNORE INTO vault_access (ref, person) VALUES (?,?)", (ref, person))

    def vault_refs_for(self, person: str, include_revoked: bool = True) -> list[str]:
        q = "SELECT ref FROM vault_access WHERE person=?"
        if not include_revoked:
            q += " AND revoked_at IS NULL"
        return [r["ref"] for r in self.db.execute(q, (person,))]

    def revoke_vault_access(self, person: str):
        self.db.execute("UPDATE vault_access SET revoked_at=? WHERE person=? AND revoked_at IS NULL", (iso(), person))

    # ---------------------------------------------------------- steps
    def step(self, brand: str, name: str) -> dict | None:
        r = self.db.execute("SELECT * FROM steps WHERE brand=? AND name=?", (brand, name)).fetchone()
        return dict(r) if r else None

    def set_step(self, brand, name, status, detail="", bump=False):
        self.db.execute(
            "INSERT INTO steps (brand, name, status, attempts, detail, updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(brand, name) DO UPDATE SET status=excluded.status, detail=excluded.detail, "
            "updated_at=excluded.updated_at, attempts=steps.attempts + ?",
            (brand, name, status, 1 if bump else 0, detail, iso(), 1 if bump else 0),
        )

    def steps(self, brand: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM steps WHERE brand=? ORDER BY rowid", (brand,))]

    # ---------------------------------------------------------- findings
    def add_finding(self, **f):
        self.db.execute(
            "INSERT INTO findings (audit_id, brand, check_name, severity, detail, verification, task_key,"
            " created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                f["audit_id"],
                f["brand"],
                f["check_name"],
                f["severity"],
                f["detail"],
                f["verification"],
                f.get("task_key"),
                iso(),
            ),
        )

    def findings(self, audit_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM findings WHERE audit_id=? ORDER BY id", (audit_id,))]
