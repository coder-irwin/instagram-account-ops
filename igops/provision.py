"""Provisioning workflow for one company-authorised Instagram brand account.

Automated: planning, handle suggestions, credential generation into the vault, task
creation and follow-up, register updates, and verification through Meta's API.
Human, by design: availability check, email alias and phone reservation (until an
integration is connected), signup and verification, two-factor setup, profile edits,
business conversion and Meta linking, and role grants in Business Suite.
"""
from __future__ import annotations

import json

from . import handles
from .app import App
from .core import iso, mask_contact
from .providers.meta import IntegrationUnavailable
from .vault import generate_password, ref
from .workflow import Blocked, Done, WaitHuman, run_steps


def _ev(task: dict | None) -> dict:
    return json.loads(task["evidence"]) if task and task.get("evidence") else {}


def steps(app: App) -> list:
    cfg, s, t, v = app.cfg, app.store, app.tasks, app.vault
    b = cfg.brand_key
    due, sdue = cfg.task_due_hours, cfg.signup_due_hours

    def task(key, kind, title, instructions, hours=due):
        return t.ensure(f"{key}:{b}", brand=b, kind=kind, title=title, instructions=instructions,
                        owner=cfg.owner, escalate_to=cfg.escalate_to, due_in_hours=hours)

    def acc():
        return s.account(b)

    # 1 ----------------------------------------------------------------------------
    def register():
        s.upsert_account(b, owner=cfg.owner, status=acc()["status"] if acc() else "provisioning")
        for p in cfg.people:
            s.upsert_person(p.email, p.name, is_test_member=p.test_member)
            for surface, role in p.roles.items():
                s.request_role(b, p.email, surface, role)
        return Done(f"register entry for {b}; {len(cfg.people)} people with least-privilege roles requested")

    # 2 ----------------------------------------------------------------------------
    def suggest():
        cands = handles.suggest(cfg.brand_name)
        s.upsert_account(b, handle_candidates=cands)
        return Done("candidates: " + ", ".join("@" + c for c in cands))

    # 3 ----------------------------------------------------------------------------
    def choose_handle():
        cands = acc()["handle_candidates"]
        tk = task("handle", "availability_check", "Check handle availability on Instagram and pick one",
                  "Open instagram.com/<handle> for each candidate in order: " + ", ".join("@" + c for c in cands)
                  + ". Pick the first that shows 'Sorry, this page isn't available'. Do not use bulk-check tools. "
                  "Complete with: handle=<chosen>.")
        if tk["status"] != "done":
            return WaitHuman(tk["key"])
        h = _ev(tk).get("handle", "").lstrip("@").lower()
        if not handles.valid(h):
            return Blocked(f"handle '{h}' from task #{tk['id']} is not a valid Instagram handle")
        s.upsert_account(b, handle=h)
        return Done(f"@{h} chosen (human-confirmed available)")

    # 4 ----------------------------------------------------------------------------
    def email_alias():
        alias = f"instagram@{cfg.company_domain}"
        tk = task("email", "it_request", f"Create company email alias {alias}",
                  f"In the test company mail admin, create {alias} routed to {cfg.owner}. "
                  "No supported mail integration is connected yet. Complete with: alias=<address>.")
        if tk["status"] != "done":
            return WaitHuman(tk["key"])
        got = _ev(tk).get("alias", "").lower()
        if not got.endswith("@" + cfg.company_domain):
            return Blocked(f"alias {mask_contact(got)} is not on the company domain")
        s.upsert_account(b, recovery_email=got)
        return Done(f"recovery email {mask_contact(got)} (company domain)")

    # 5 ----------------------------------------------------------------------------
    def phone():
        tk = task("phone", "it_request", "Reserve and assign a company-controlled phone number",
                  "Reserve a number on the test company's telephony account and assign it to this brand. "
                  "Never use a personal number. Complete with: phone=<E.164 number>.")
        if tk["status"] != "done":
            return WaitHuman(tk["key"])
        num = _ev(tk).get("phone", "")
        if num.replace(" ", "") not in [p.replace(" ", "") for p in cfg.company_phones]:
            return Blocked(f"phone {mask_contact(num)} is not on the company-controlled list in config")
        s.upsert_account(b, recovery_phone=num)
        return Done(f"recovery phone {mask_contact(num)} (company-controlled)")

    # 6 ----------------------------------------------------------------------------
    def credentials():
        pw_ref = ref(b, "instagram", "password")
        if not v.exists(pw_ref):                        # idempotent: never regenerate on resume
            v.put(pw_ref, generate_password(), meta={"purpose": "instagram login"})
        refs = {**acc()["vault_refs"], "password": pw_ref,
                "backup_codes": ref(b, "instagram", "backup_codes")}
        s.upsert_account(b, vault_refs=refs)
        s.grant_vault_access(pw_ref, cfg.owner)
        for p in cfg.people:
            for item in p.vault_access:
                if item in refs:
                    s.grant_vault_access(refs[item], p.email)
        return Done(f"password generated in vault at {pw_ref}")

    # 7 ----------------------------------------------------------------------------
    def signup():
        a = acc()
        tk = task("signup", "signup", f"Sign up @{a['handle']} on Instagram and complete verification",
                  f"FIRST search Instagram for @{a['handle']}. If it already exists and is ours, do NOT create "
                  f"another account: complete with existing=yes. Otherwise sign up in the official app or "
                  f"website with email {a['recovery_email']}, phone {mask_contact(a['recovery_phone'])}, and the "
                  f"password from the vault at {a['vault_refs']['password']}. Complete any verification yourself. "
                  f"Complete with: handle=<handle> created=<new|existing>.", hours=sdue)
        if tk["status"] != "done":
            return WaitHuman(tk["key"])
        ev = _ev(tk)
        if ev.get("handle", "").lstrip("@").lower() != a["handle"]:
            return Blocked(f"signup task reports @{ev.get('handle')} but the register says @{a['handle']}")
        s.upsert_account(b, status="created")
        return Done(f"@{a['handle']} exists ({ev.get('created', 'new')}), human-confirmed")

    # 8 ----------------------------------------------------------------------------
    def two_factor():
        a = acc()
        codes_ref = a["vault_refs"]["backup_codes"]
        tk = task("2fa", "security", f"Enable two-factor authentication on @{a['handle']}",
                  "Turn on two-factor with an authentication app (preferred over SMS). Save the backup codes "
                  f"straight into the vault: `igops vault put {codes_ref} --prompt`. Never paste them into chat, "
                  "email or the task. Complete with: method=<authenticator_app|sms>.")
        if tk["status"] != "done":
            return WaitHuman(tk["key"])
        if not v.exists(codes_ref):
            tk2 = task("2fa-codes", "security", "Store the backup codes in the vault",
                       f"Two-factor is on but no backup codes are in the vault. Run `igops vault put {codes_ref} "
                       "--prompt`, then complete this task with stored=yes.")
            if tk2["status"] != "done" or not v.exists(codes_ref):
                return WaitHuman(tk2["key"])
        v.mark(codes_ref, exposed=False)
        s.upsert_account(b, two_factor_method=_ev(tk).get("method", "authenticator_app"))
        return Done(f"two-factor on ({_ev(tk).get('method')}); backup codes stored at {codes_ref}")

    # 9 ----------------------------------------------------------------------------
    def business_link():
        a = acc()
        tk = task("business", "linking", f"Convert @{a['handle']} to a business account and link to Meta",
                  "In the Instagram app: Settings > Account type > Switch to professional > Business, "
                  f"category '{cfg.category}'. Then in Meta Business Suite for the test business "
                  f"({cfg.meta_business_id or 'business id pending'}), add the Instagram account as an asset. "
                  "Complete with: ig_user_id=<numeric id shown in Business Settings>.")
        if tk["status"] != "done":
            return WaitHuman(tk["key"])
        ig_id = str(_ev(tk).get("ig_user_id", "")).strip()
        if not ig_id.isdigit():
            return Blocked("business conversion done but the Instagram account id was not recorded")
        s.upsert_account(b, ig_user_id=ig_id, status="business_linked")
        return Done(f"business account linked, Instagram id {ig_id}")

    # 10 ---------------------------------------------------------------------------
    def profile():
        a = acc()
        want = {"name": cfg.brand_name, "biography": cfg.bio, "website": cfg.website}
        if len(cfg.bio) > 150:
            return Blocked(f"bio is {len(cfg.bio)} characters, over Instagram's 150")
        tk = task("profile", "profile", f"Complete the @{a['handle']} profile",
                  f"Set name '{cfg.brand_name}', bio '{cfg.bio}' ({len(cfg.bio)}/150), link {cfg.website}, "
                  f"category '{cfg.category}', and the profile image {cfg.profile_image} from the brand kit. "
                  "Complete with: done=yes.")
        if tk["status"] != "done":
            return WaitHuman(tk["key"])
        s.upsert_account(b, profile={**want, "category": cfg.category, "image": cfg.profile_image,
                                     "verified_how": "human-confirmed"})
        return Done("profile set (human-confirmed; API check runs in final verification)")

    # 11 ---------------------------------------------------------------------------
    def roles():
        pending = []
        for r in s.roles(b):
            if r["state"] != "requested":
                continue
            tk = task(f"role-{r['person']}-{r['surface']}", "access",
                      f"Grant {r['role']} on {r['surface']} to {r['person']}",
                      f"In Meta Business Suite > Settings > People, give {r['person']} only the "
                      f"'{r['role']}' permission on the Instagram account. Nothing broader. Complete with: granted=yes.")
            if tk["status"] == "done":
                s.set_role_state(b, r["person"], r["surface"], "granted", "human-confirmed")
            else:
                pending.append(tk["key"])
        if pending:
            return WaitHuman(pending[0], f"{len(pending)} role grants pending")
        return Done(f"{len(s.roles(b))} least-privilege roles granted (human-confirmed)")

    # 12 ---------------------------------------------------------------------------
    def verify():
        a = acc()
        try:
            prof = app.meta.instagram_profile(a["ig_user_id"])
        except IntegrationUnavailable as exc:
            return Blocked(f"cannot verify through Meta API: {exc}. Resume: add the token to the vault "
                           f"at {cfg.meta_token_ref}, then rerun `igops provision`.")
        mism = []
        if prof.get("username", "").lower() != a["handle"]:
            mism.append(f"username is {prof.get('username')}")
        if (prof.get("biography") or "") != cfg.bio:
            mism.append("bio differs from config")
        if (prof.get("website") or "").rstrip("/") != cfg.website.rstrip("/"):
            mism.append("website differs")
        how = "integration-verified (simulated)" if getattr(app.meta, "simulated", False) else "integration-verified"
        if mism:
            task("profile-fix", "profile", "Fix profile mismatches found by verification", "; ".join(mism))
            return Blocked("profile does not match: " + "; ".join(mism))
        s.upsert_account(b, status="active", last_verified_at=iso(), last_verified_how=how)
        return Done(f"@{a['handle']} verified via {app.meta.name}: username, bio and website match")

    return [("register", register), ("suggest_handles", suggest), ("choose_handle", choose_handle),
            ("email_alias", email_alias), ("phone", phone), ("credentials", credentials),
            ("human_signup", signup), ("two_factor", two_factor), ("business_link", business_link),
            ("profile", profile), ("roles", roles), ("verify", verify)]


def run(app: App, holder: str = "cli") -> str:
    lease = f"provision:{app.cfg.brand_key}"
    if not app.store.acquire(lease, holder):
        app.log.write("run.locked", msg="another provisioning run holds the lease; not starting a second one")
        return "locked"
    try:
        app.log.write("run.start", msg=f"provisioning {app.cfg.brand_key} ({app.cfg.mode})")
        outcome = run_steps(app.store, app.log, app.cfg.brand_key, steps(app))
        app.log.write("run.end", outcome=outcome, msg=f"provisioning outcome: {outcome}")
        return outcome
    finally:
        app.store.release(lease, holder)
