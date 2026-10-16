"""Write the deliverable reports, then prove none of them contains a secret."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .app import App
from .core import iso, mask_contact, redact


def _w(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(text))


def register(app: App) -> Path:
    out = app.state_dir / "reports"
    rows = []
    for a in app.store.accounts():
        roles = app.store.roles(a["brand"])
        rows.append({
            "brand": a["brand"], "handle": a["handle"] and "@" + a["handle"], "owner": a["owner"],
            "recovery_email": mask_contact(a["recovery_email"]), "recovery_phone": mask_contact(a["recovery_phone"]),
            "two_factor_method": a["two_factor_method"], "vault_refs": a["vault_refs"],
            "people_and_roles": [{"person": r["person"], "surface": r["surface"], "role": r["role"],
                                  "state": r["state"], "verified": r["verified_how"]} for r in roles],
            "status": a["status"], "blockers": [b["text"] for b in a["blockers"]],
            "last_verified_at": a["last_verified_at"], "last_verified_how": a["last_verified_how"],
        })
    _w(out / "account_register.json", json.dumps({"generated_at": iso(), "mode": app.cfg.mode, "accounts": rows}, indent=2))
    md = [f"# Account register\n\nGenerated {iso()}. Mode: **{app.cfg.mode}**. "
          "Contacts are partly masked; secrets appear only as vault references.\n"]
    for r in rows:
        md += [f"## {r['brand']} {r['handle'] or '(handle pending)'}\n", "| Field | Value |", "|---|---|"]
        for k in ("owner", "recovery_email", "recovery_phone", "two_factor_method", "status",
                  "last_verified_at", "last_verified_how"):
            md.append(f"| {k.replace('_', ' ')} | {r[k] or '—'} |")
        md.append(f"| vault references | {', '.join(f'{k}: `{v}`' for k, v in r['vault_refs'].items()) or '—'} |")
        md.append(f"| blockers | {'; '.join(r['blockers']) or 'none'} |\n")
        md += ["| Person | Surface | Role | State | Verified |", "|---|---|---|---|---|"]
        md += [f"| {p['person']} | {p['surface']} | {p['role']} | {p['state']} | {p['verified']} |"
               for p in r["people_and_roles"]]
    _w(out / "account_register.md", "\n".join(md) + "\n")
    return out / "account_register.md"


def audit(app: App, rep: dict, name: str = "audit_report") -> Path:
    out = app.state_dir / "reports"
    _w(out / f"{name}.json", json.dumps(rep, indent=2))
    md = [f"# Audit report {rep['audit_id']}\n", f"Ran {rep['ran_at']} in **{rep['mode']}** mode. "
          f"{rep['checks_run']} checks, {len(rep['findings'])} findings. Schedule: {rep['schedule']}.\n",
          "Verification labels: **integration-verified** = checked by code against a system of record; "
          "**human-confirmed** = rests on a completed task; **unverified** = no evidence yet. "
          "In simulation mode the Meta API is simulated and labelled so.\n",
          "| Severity | Finding | Verified | Follow-up task |", "|---|---|---|---|"]
    md += [f"| {f['severity']} | {f['detail']} | {f['verification']} | #{f['task']} |" for f in rep["findings"]]
    if not rep["findings"]:
        md.append("| — | no findings | — | — |")
    _w(out / f"{name}.md", "\n".join(md) + "\n")
    return out / f"{name}.md"


def offboarding(app: App, rep: dict, name: str = "offboarding_report") -> Path:
    out = app.state_dir / "reports"
    _w(out / f"{name}.json", json.dumps(rep, indent=2))
    s = rep["summary"]
    md = [f"# Offboarding report: {rep['person']}\n", f"Ran {rep['ran_at']} in **{rep['mode']}** mode. "
          f"{s['completed']} completed, {s['pending']} pending, {s['blocked']} blocked.\n",
          "| Action | Status | Detail |", "|---|---|---|"]
    md += [f"| {a['action']} | {a['status']} | {a['detail']} |" for a in rep["actions"]]
    _w(out / f"{name}.md", "\n".join(md) + "\n")
    return out / f"{name}.md"


def tasks(app: App) -> Path:
    out = app.state_dir / "reports"
    md = ["# Human tasks\n", "| # | Task | Owner | Due | Status | Reminders | Escalated |", "|---|---|---|---|---|---|---|"]
    for t in app.tasks.all():
        md.append(f"| {t['id']} | {t['title']} | {t['owner']} | {t['due_at'][:16]} | {t['status']} | "
                  f"{t['reminders_sent']} | {'yes' if t['escalated'] else 'no'} |")
    _w(out / "tasks.md", "\n".join(md) + "\n")
    return out / "tasks.md"


def run_log(app: App) -> Path:
    out = app.state_dir / "reports" / "run_log_redacted.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(app.state_dir / "run_log.jsonl", out)     # already redacted line by line at write time
    return out


def secret_scan(app: App) -> dict:
    """Decrypt every secret in the vault and search every output file for it."""
    values = []
    for r in app.vault.refs():
        values.append(app.vault._data[r]["value"])
        if "@" in app.vault._data[r]["value"] and "://" in app.vault._data[r]["value"]:
            # also catch the password part of proxy URLs on its own
            values.append(app.vault._data[r]["value"].split("://", 1)[1].split("@", 1)[0].split(":", 1)[-1])
    files = [p for p in app.state_dir.rglob("*") if p.is_file() and p.suffix in (".md", ".json", ".jsonl", ".log", ".txt")]
    hits = [(str(p.relative_to(app.state_dir)), i) for p in files for i, v in enumerate(values)
            if len(v) >= 6 and v in p.read_text(errors="ignore")]
    result = {"files_scanned": len(files), "secrets_checked": len(values), "leaks": hits}
    app.log.write("secret_scan", files=len(files), secrets=len(values), leaks=len(hits),
                  msg=f"secret scan: {len(values)} secrets checked across {len(files)} files, {len(hits)} leaks")
    return result
