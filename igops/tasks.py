"""Human tasks: owner, deadline, reminders, escalation, and idempotent creation.

A task is keyed by the real-world need (for example `signup:acme`), not by the run that
noticed it. Asking twice returns the same task, so a crash-and-resume never creates a
second signup request, and therefore never a second Instagram account.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .core import RunLog, iso, now
from .store import Store

REMIND_BEFORE = timedelta(hours=4)  # first nudge when a deadline is this close
REMIND_EVERY = timedelta(hours=12)  # at most one reminder per window
MAX_REMINDERS = 3


class Notifier:
    """Delivers task assignments, reminders and escalations.

    Always writes to the run log. If an SMTP URL is stored in the vault at
    `smtp_ref` (smtps://user:pass@host:465/from@domain), it also emails the person.
    Delivery failure never blocks the workflow; it is logged and retried by the next sweep."""

    def __init__(self, log: RunLog, vault=None, smtp_ref: str | None = None):
        self.log, self.vault, self.smtp_ref = log, vault, smtp_ref
        self.sent: list[dict] = []

    def send(self, to: str, subject: str, body: str, kind: str):
        self.sent.append({"to": to, "subject": subject, "kind": kind, "at": iso()})
        self.log.write(f"notify.{kind}", to=to, msg=f"to {to}: {subject}")
        if self.vault is not None and self.smtp_ref and self.vault.exists(self.smtp_ref):
            try:
                self._email(to, subject, body)
            except Exception as exc:
                self.log.write("notify.failed", to=to, msg=f"email to {to} failed: {type(exc).__name__}")

    def _email(self, to: str, subject: str, body: str):
        import smtplib
        from email.message import EmailMessage
        from urllib.parse import urlsplit

        u = urlsplit(self.vault.get(self.smtp_ref))
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = u.path.lstrip("/"), to, subject
        msg.set_content(body)
        with smtplib.SMTP_SSL(u.hostname, u.port or 465, timeout=10) as s:
            s.login(u.username, u.password)
            s.send_message(msg)


class Tasks:
    def __init__(self, store: Store, log: RunLog, notifier: Notifier):
        self.s, self.log, self.n = store, log, notifier

    def ensure(
        self,
        key: str,
        *,
        brand: str,
        kind: str,
        title: str,
        instructions: str,
        owner: str,
        escalate_to: str,
        due_in_hours: float,
        reopen: bool = False,
    ) -> dict:
        """Create the task once. Later calls return the existing one unchanged, unless
        `reopen` is set and the task was closed: then the same task is reopened (used when
        an audit finds a problem that had been fixed has come back)."""
        row = self.s.db.execute("SELECT * FROM tasks WHERE key=?", (key,)).fetchone()
        if row and not (reopen and row["status"] in ("done", "cancelled")):
            return dict(row)
        if row:
            self.s.db.execute(
                "UPDATE tasks SET status='open', title=?, instructions=?, due_at=?, reminders_sent=0,"
                " escalated=0, completed_at=NULL WHERE key=?",
                (title, instructions, iso(now() + timedelta(hours=due_in_hours)), key),
            )
            t = dict(self.s.db.execute("SELECT * FROM tasks WHERE key=?", (key,)).fetchone())
            self.log.write("task.reopened", task=t["id"], key=key, msg=f"#{t['id']} reopened: {title}")
            self.n.send(owner, f"Reopened task #{t['id']}: {title}", instructions, "assigned")
            return t
        due = now() + timedelta(hours=due_in_hours)
        self.s.db.execute(
            "INSERT INTO tasks (key, brand, kind, title, instructions, owner, escalate_to, due_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (key, brand, kind, title, instructions, owner, escalate_to, iso(due), iso()),
        )
        t = dict(self.s.db.execute("SELECT * FROM tasks WHERE key=?", (key,)).fetchone())
        self.log.write(
            "task.created",
            task=t["id"],
            key=key,
            owner=owner,
            due=t["due_at"],
            msg=f"#{t['id']} {title} -> {owner}, due {t['due_at'][:16]}",
        )
        self.n.send(owner, f"New task #{t['id']}: {title}", instructions, "assigned")
        return t

    def get(self, key: str) -> dict | None:
        r = self.s.db.execute("SELECT * FROM tasks WHERE key=?", (key,)).fetchone()
        return dict(r) if r else None

    def by_id(self, task_id: int) -> dict | None:
        r = self.s.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r) if r else None

    def complete(self, task_id: int, evidence: dict, by: str) -> dict:
        """Close a task with evidence. Evidence must never contain secret values; the CLI
        rejects keys that look like secrets, and secrets go to the vault instead."""
        bad = [k for k in evidence if any(w in k.lower() for w in ("password", "code", "secret", "token"))]
        if bad:
            raise ValueError(f"evidence must not contain secrets ({bad}); store them with `igops vault put`")
        t = self.by_id(task_id)
        if not t:
            raise KeyError(f"no task #{task_id}")
        if t["status"] == "done":
            return t  # idempotent
        self.s.db.execute(
            "UPDATE tasks SET status='done', evidence=?, completed_at=? WHERE id=?",
            (json.dumps({**evidence, "completed_by": by}), iso(), task_id),
        )
        self.log.write("task.completed", task=task_id, by=by, msg=f"#{task_id} {t['title']} (by {by})")
        done = self.by_id(task_id)
        assert done is not None
        return done

    def cancel(self, key: str, reason: str):
        self.s.db.execute(
            "UPDATE tasks SET status='cancelled', evidence=? WHERE key=? AND status='open'",
            (json.dumps({"reason": reason}), key),
        )

    def open_tasks(self) -> list[dict]:
        return [dict(r) for r in self.s.db.execute("SELECT * FROM tasks WHERE status='open' ORDER BY due_at")]

    def all(self) -> list[dict]:
        return [dict(r) for r in self.s.db.execute("SELECT * FROM tasks ORDER BY id")]

    def sweep(self) -> dict:
        """Send due reminders and escalate overdue tasks. Safe to run as often as you like."""
        out: dict[str, list[int]] = {"reminded": [], "escalated": []}
        t_now = now()
        for t in self.open_tasks():
            due = datetime.fromisoformat(t["due_at"])
            if t_now > due and not t["escalated"]:
                self.s.db.execute("UPDATE tasks SET escalated=1 WHERE id=?", (t["id"],))
                self.n.send(
                    t["escalate_to"],
                    f"OVERDUE task #{t['id']}: {t['title']}",
                    f"Owner {t['owner']} has not completed this. It was due {t['due_at']}.",
                    "escalation",
                )
                self.log.write(
                    "task.escalated",
                    task=t["id"],
                    to=t["escalate_to"],
                    msg=f"#{t['id']} overdue, escalated to {t['escalate_to']}",
                )
                out["escalated"].append(t["id"])
            elif due - t_now <= REMIND_BEFORE or t_now > due:
                last = t["reminders_sent"]
                window_start = due - REMIND_BEFORE + REMIND_EVERY * last
                if last < MAX_REMINDERS and t_now >= window_start:
                    self.s.db.execute("UPDATE tasks SET reminders_sent=reminders_sent+1 WHERE id=?", (t["id"],))
                    self.n.send(t["owner"], f"Reminder: task #{t['id']} due {t['due_at'][:16]}", t["title"], "reminder")
                    out["reminded"].append(t["id"])
        return out
