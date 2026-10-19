"""Weekly security audit. Every finding states how it was verified:

  integration-verified   checked against a system of record by code (vault, Meta API)
  human-confirmed        rests on a person's completed task with evidence
  unverified             no evidence either way: treat as a risk until confirmed

Each finding opens one follow-up task, keyed so a repeated audit never duplicates it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .app import App
from .core import iso, mask_contact, now

SCHEDULE_CRON = "0 9 * * MON  cd {root} && uv run python -m igops audit --scheduled >> state/audit_cron.log 2>&1"


def run(app: App) -> dict:
    cfg, s, v = app.cfg, app.store, app.vault
    audit_id = "audit-" + now().strftime("%Y%m%dT%H%M%S")
    findings, checks = [], 0

    def finding(brand, check, severity, detail, verification, fix):
        key = f"audit:{brand}:{check}"
        t = app.tasks.ensure(key, brand=brand, kind="audit_followup", title=f"Audit: {detail}",
                             instructions=fix, owner=cfg.owner, escalate_to=cfg.escalate_to,
                             due_in_hours=72 if severity != "high" else 24, reopen=True)
        s.add_finding(audit_id=audit_id, brand=brand, check_name=check, severity=severity,
                      detail=detail, verification=verification, task_key=key)
        findings.append({"brand": brand, "check": check, "severity": severity, "detail": detail,
                         "verification": verification, "task": t["id"]})

    for a in s.accounts():
        b = a["brand"]
        sim = " (simulated)" if cfg.simulation else ""

        checks += 1   # recovery email on company domain
        if not a["recovery_email"]:
            finding(b, "recovery_email_missing", "high", "no recovery email recorded", "unverified",
                    "Confirm the recovery email on Instagram and record it.")
        elif not a["recovery_email"].lower().endswith("@" + cfg.company_domain):
            finding(b, "recovery_email_external", "high",
                    f"recovery email {mask_contact(a['recovery_email'])} is not a company address",
                    "human-confirmed", "Change the Instagram recovery email to the company alias.")

        checks += 1   # recovery phone company-controlled
        listed = [p.replace(" ", "") for p in cfg.company_phones]
        if not a["recovery_phone"]:
            finding(b, "recovery_phone_missing", "medium", "no recovery phone recorded", "unverified",
                    "Confirm the recovery phone on Instagram and record it.")
        elif a["recovery_phone"].replace(" ", "") not in listed:
            finding(b, "recovery_phone_external", "high",
                    f"recovery phone {mask_contact(a['recovery_phone'])} is not company-controlled",
                    "human-confirmed", "Replace it with a reserved company number.")

        checks += 1   # backup codes present and not exposed
        codes = a["vault_refs"].get("backup_codes")
        meta = v.metadata(codes) if codes else {}
        if not codes or not v.exists(codes):
            finding(b, "backup_codes_missing", "high", "no backup codes in the vault", "integration-verified",
                    "Generate backup codes on Instagram and store them with `igops vault put ... --prompt`.")
        elif meta.get("exposed"):
            finding(b, "backup_codes_exposed", "high", "backup codes were exposed and not yet replaced",
                    "integration-verified", "Regenerate backup codes on Instagram and store the new set.")

        checks += 1   # two-factor
        if not a["two_factor_method"]:
            finding(b, "two_factor_off", "high", "two-factor is not recorded as enabled", "unverified",
                    "Enable two-factor with an authentication app and confirm.")
        elif a["two_factor_method"] == "sms":
            finding(b, "two_factor_sms", "medium", "two-factor uses SMS, which is weaker than an app",
                    "human-confirmed", "Switch two-factor to an authentication app.")

        checks += 1   # setup complete
        if a["status"] != "active":
            finding(b, "setup_incomplete", "medium", f"setup incomplete (status {a['status']})",
                    "human-confirmed", "Finish the open provisioning tasks.")

        checks += 1   # stale verification
        lv = a["last_verified_at"]
        if not lv or now() - datetime.fromisoformat(lv) > timedelta(days=cfg.stale_verification_days):
            finding(b, "verification_stale", "low", f"last verified {lv or 'never'}",
                    ("integration-verified" + sim) if lv else "unverified",
                    "Run `igops provision` to re-verify the profile through the Meta API.")

        # people and access
        people = {p["email"]: p for p in s.people()}
        for r in s.roles(b):
            checks += 1
            person = people.get(r["person"], {})
            if r["state"] == "granted" and person.get("status") == "departed":
                finding(b, f"departed_access:{r['person']}", "high",
                        f"{r['person']} has left but still holds {r['role']}", r["verified_how"],
                        "Run offboarding for this person.")
            elif r["state"] == "granted" and (not r["last_confirmed_at"] or
                    now() - datetime.fromisoformat(r["last_confirmed_at"]) > timedelta(days=cfg.stale_access_days)):
                finding(b, f"stale_access:{r['person']}", "medium",
                        f"access for {r['person']} not reconfirmed in {cfg.stale_access_days} days",
                        r["verified_how"], "Confirm the role is still needed, or remove it.")
            elif r["state"] == "removal_pending":
                finding(b, f"removal_pending:{r['person']}", "high",
                        f"role removal for {r['person']} not confirmed yet", "unverified",
                        "Complete the open offboarding task.")

    # Close follow-ups whose problem is gone, so the task list reflects reality.
    current = {f"audit:{f['brand']}:{f['check']}" for f in findings}
    resolved = []
    for t in app.tasks.open_tasks():
        if t["kind"] == "audit_followup" and t["key"] not in current:
            app.tasks.cancel(t["key"], f"resolved: check passed on {audit_id}")
            resolved.append(t["id"])
            app.log.write("audit.resolved", task=t["id"], msg=f"#{t['id']} closed: check now passes")

    report = {"audit_id": audit_id, "resolved_followups": resolved, "ran_at": iso(), "mode": cfg.mode, "checks_run": checks,
              "findings": findings, "schedule": "weekly, Mondays 09:00 (see README for the cron line)"}
    (app.state_dir / "reports").mkdir(exist_ok=True)
    app.log.write("audit.done", audit=audit_id, findings=len(findings),
                  msg=f"{audit_id}: {checks} checks, {len(findings)} findings")
    return report
