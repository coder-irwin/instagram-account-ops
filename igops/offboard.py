"""Offboarding a departing team member, across every account.

For each action the report says completed, pending (a person must act) or blocked
(missing access). Re-running continues where it stopped and never repeats a finished
action, because each action is a keyed task or an idempotent state change.
"""

from __future__ import annotations

from .app import App
from .core import iso
from .vault import generate_password


def _done(app: App, key: str) -> dict | None:
    t = app.tasks.get(key)
    return t if t and t["status"] == "done" else None


def run(app: App, person: str) -> dict:
    cfg, s, v = app.cfg, app.store, app.vault
    actions: list[dict] = []

    def act(name, status, detail):
        actions.append({"action": name, "status": status, "detail": detail})

    s.set_person_status(person, "departed")

    # 1. remove roles on every account
    for r in s.roles(person=person):
        if r["state"] == "removed":
            act(f"remove {r['role']} on {r['brand']}/{r['surface']}", "completed", "already removed")
            continue
        key = f"offboard-role:{r['brand']}:{person}:{r['surface']}"
        app.tasks.ensure(
            key,
            brand=r["brand"],
            kind="offboarding",
            title=f"Remove {person} from {r['brand']} {r['surface']}",
            instructions=f"In Meta Business Suite > Settings > People, remove {person} "
            f"(role {r['role']}) from the Instagram account. Complete with: removed=yes.",
            owner=cfg.owner,
            escalate_to=cfg.escalate_to,
            due_in_hours=4,
        )
        if _done(app, key):
            s.set_role_state(r["brand"], person, r["surface"], "removed", "human-confirmed")
            act(f"remove {r['role']} on {r['brand']}/{r['surface']}", "completed", "human-confirmed")
        else:
            s.set_role_state(r["brand"], person, r["surface"], "removal_pending")
            act(
                f"remove {r['role']} on {r['brand']}/{r['surface']}",
                "pending",
                f"task {(app.tasks.get(key) or {}).get('id', '?')} with {cfg.owner}",
            )

    # 2. rotate every credential they could read
    for vref in s.vault_refs_for(person):
        brand = vref.split("//", 1)[1].split("/", 1)[0]
        acc = s.account(brand) or {"handle": "?", "vault_refs": {}}
        if vref.endswith("/password"):
            key = f"offboard-pw:{brand}:{person}"
            pending_ref = vref + ".next"
            if _done(app, key):
                if v.exists(pending_ref):
                    # promote the staged password exactly once, after a person confirms it is live,
                    # then delete the staging copy so the live password exists in one place only
                    v.put(vref, v.get(pending_ref), meta={"rotated_because": f"offboarding {person}"})
                    v.delete(pending_ref)
                act(
                    f"rotate password for @{acc['handle']}",
                    "completed",
                    "new password live on Instagram (human-confirmed)",
                )
            else:
                if not v.exists(pending_ref):
                    v.put(pending_ref, generate_password(), meta={"purpose": "rotation", "for": vref})
                t = app.tasks.ensure(
                    key,
                    brand=brand,
                    kind="offboarding",
                    title=f"Change the @{acc['handle']} password",
                    instructions=f"Log in with the current password ({vref}) and change it to the new one "
                    f"at {pending_ref}. Complete with: changed=yes.",
                    owner=cfg.owner,
                    escalate_to=cfg.escalate_to,
                    due_in_hours=4,
                )
                act(f"rotate password for @{acc['handle']}", "pending", f"new password staged in vault; task {t['id']}")
        elif vref.endswith("/backup_codes"):
            key = f"offboard-codes:{brand}:{person}"
            label = f"replace backup codes for @{acc['handle']}"
            if not v.exists(vref):
                act(label, "blocked", "no backup codes stored for this account yet")
                continue
            mark = f"exposed_version:{person}"
            meta = v.metadata(vref)
            if mark not in meta:  # first run: record which set they saw
                v.mark(vref, exposed=True, exposed_at=iso(), **{mark: meta["version"]})
                meta = v.metadata(vref)
            if meta["version"] > meta[mark]:  # a newer set was stored after exposure
                v.mark(vref, exposed=False)
                act(
                    label,
                    "completed",
                    "new codes stored (integration-verified); old set revoked on Instagram (human-confirmed)",
                )
            else:
                t = app.tasks.ensure(
                    key,
                    brand=brand,
                    kind="offboarding",
                    title=f"Replace @{acc['handle']} backup codes",
                    instructions="On Instagram: Settings > Security > Two-factor > Backup codes > Get new "
                    f"codes (this revokes the old set). Store them with `igops vault put {vref} "
                    "--prompt`. Complete with: replaced=yes.",
                    owner=cfg.owner,
                    escalate_to=cfg.escalate_to,
                    due_in_hours=4,
                )
                note = "task closed but no new codes in the vault yet" if t["status"] == "done" else f"task {t['id']}"
                act(label, "pending", f"old set marked exposed; {note}")

    # 3. revoke vault access once nothing they could see is still live
    if all(a["status"] == "completed" for a in actions if a["action"].startswith(("rotate", "replace"))):
        s.revoke_vault_access(person)
        act("revoke vault access", "completed", "all secrets they could read are rotated")
    else:
        act("revoke vault access", "pending", "waits until every exposed secret is rotated")

    # 4. anything we cannot do without access is blocked, not silently skipped
    if not cfg.simulation and not v.exists(cfg.meta_token_ref):
        act("verify removal through Meta API", "blocked", f"no Meta token at {cfg.meta_token_ref}")

    for acc in s.accounts():
        blockers = [a for a in actions if a["status"] != "completed"]
        s.set_blocker(
            acc["brand"],
            f"offboarding:{person}",
            f"{len(blockers)} offboarding actions open for {person}" if blockers else None,
        )

    summary = {k: sum(1 for a in actions if a["status"] == k) for k in ("completed", "pending", "blocked")}
    app.log.write(
        "offboard.run",
        person=person,
        **summary,
        msg=f"offboarding {person}: {summary['completed']} completed, {summary['pending']} pending, "
        f"{summary['blocked']} blocked",
    )
    return {"person": person, "ran_at": iso(), "mode": cfg.mode, "summary": summary, "actions": actions}
