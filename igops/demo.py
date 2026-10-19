"""End-to-end demonstration in simulation mode.

It runs the real code paths. Only two things are simulated, and both are labelled
everywhere: the people completing tasks, and the Meta API. Nothing touches Instagram.

Scenes:
  1. provisioning pauses at each human handoff; a simulated owner completes tasks
  2. a reminder, then an escalation when the signup task goes overdue
  3. a crash in the middle of the run; the rerun resumes with no duplicate tasks
  4. a transient Meta API error is retried, then verification succeeds
  5. weekly audit, run once, with labelled findings and follow-up tasks
  6. offboarding the temporary tester: pending, then completed after humans act
  7. proxies: health checks, a pinned session, scheduled rotation, failover, and a
     security challenge that pauses for human review
  8. reports and a secret scan across every output
"""
from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from . import audit, offboard, provision, reports
from .app import build
from .core import advance
from .neutral import NeutralEndpoint, TestProxy
from .proxy import ProxyDown, ProxyPool, SecurityChallenge
from .vault import ref
from .workflow import Interrupted


def banner(text: str):
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def human(app, key_prefix: str, **evidence):
    """The simulated owner completes the open task whose key starts with key_prefix."""
    t = next(t for t in app.tasks.open_tasks() if t["key"].startswith(key_prefix))
    app.tasks.complete(t["id"], evidence, by=f"{app.cfg.owner} (simulated)")
    return t


def run(config: str = "config/brand.toml", state: str = "state/demo") -> dict:
    shutil.rmtree(state, ignore_errors=True)
    app = build(config, state)
    cfg, b = app.cfg, app.cfg.brand_key
    assert cfg.simulation, "the demo only runs in simulation mode"

    banner("1. Provisioning: automation runs until it needs a person")
    provision.run(app)
    human(app, "handle:", handle=app.store.account(b)["handle_candidates"][1])
    provision.run(app)
    human(app, "email:", alias=f"instagram@{cfg.company_domain}")
    provision.run(app)
    human(app, "phone:", phone=cfg.company_phones[0])
    provision.run(app)                      # generates the password, then waits for signup

    banner("2. Nobody does the signup: reminder, then escalation")
    advance(cfg.signup_due_hours - 3)
    print("sweep:", app.tasks.sweep())
    advance(4)
    print("sweep:", app.tasks.sweep())
    print("sweep again (no repeat escalation):", app.tasks.sweep())

    banner("3. Signup done; then the process crashes mid-run and resumes")
    handle = app.store.account(b)["handle"]
    human(app, "signup:", handle=handle, created="new")
    signup_tasks_before = sum(1 for t in app.tasks.all() if t["key"].startswith("signup:"))
    original = provision.steps

    def crashing(app_):
        st = original(app_)
        return [(n, (lambda: (_ for _ in ()).throw(Interrupted("simulated crash: process killed")))
                 if n == "two_factor" else f) for n, f in st]
    provision.steps = crashing
    try:
        provision.run(app)
    except Interrupted as exc:
        app.log.write("run.crashed", msg=f"{exc} (step two_factor left as 'running')")
    finally:
        provision.steps = original
    provision.run(app)                           # resumes at two_factor; earlier steps are skipped
    signup_tasks_after = sum(1 for t in app.tasks.all() if t["key"].startswith("signup:"))
    print(f"signup tasks before crash: {signup_tasks_before}, after resume: {signup_tasks_after} (no duplicate account)")

    banner("3b. An audit run mid-provisioning flags the security gaps")
    early = audit.run(app)
    reports.audit(app, early, "audit_report_mid_provisioning")
    for f in early["findings"]:
        print(f"  [{f['severity']}] {f['detail']}  ({f['verification']}, task #{f['task']})")

    banner("4. Two-factor, business link, profile, roles; a transient API error on verification")
    human(app, "2fa:", method="authenticator_app")
    provision.run(app)                                       # no codes in vault yet -> follow-up task
    app.vault.put(app.store.account(b)["vault_refs"]["backup_codes"],
                  " ".join(f"{secrets.randbelow(10**8):08d}" for _ in range(8)),
                  meta={"purpose": "instagram backup codes"})   # what `igops vault put --prompt` does
    human(app, "2fa-codes:", stored="yes")
    provision.run(app)
    human(app, "business:", ig_user_id="17841400000000001")
    provision.run(app)
    human(app, "profile:", done="yes")
    provision.run(app)
    for t in [t for t in app.tasks.open_tasks() if t["key"].startswith("role-")]:
        app.tasks.complete(t["id"], {"granted": "yes"}, by=f"{cfg.owner} (simulated)")
    app.meta.set_profile("17841400000000001", username=handle, name=cfg.brand_name,
                         biography=cfg.bio, website=cfg.website)
    app.meta.fail_next = 1
    outcome = provision.run(app)
    print("provisioning outcome:", outcome)

    banner("5. Weekly audit after provisioning (earlier follow-ups auto-close when fixed)")
    audit_rep = audit.run(app)
    reports.audit(app, audit_rep)

    banner("6. Offboarding the temporary tester")
    tester = next(p.email for p in cfg.people if p.test_member)
    first = offboard.run(app, tester)
    reports.offboarding(app, first, "offboarding_report_first_pass")
    audit_mid = audit.run(app)
    reports.audit(app, audit_mid, "audit_report_during_offboarding")
    for t in [t for t in app.tasks.open_tasks() if t["key"].startswith(("offboard-role", "offboard-pw"))]:
        app.tasks.complete(t["id"], {"done": "yes"}, by=f"{cfg.owner} (simulated)")
    codes_ref = app.store.account(b)["vault_refs"]["backup_codes"]
    app.vault.put(codes_ref, " ".join(f"{secrets.randbelow(10**8):08d}" for _ in range(8)))  # new set
    for t in [t for t in app.tasks.open_tasks() if t["key"].startswith("offboard-codes")]:
        app.tasks.complete(t["id"], {"replaced": "yes"}, by=f"{cfg.owner} (simulated)")
    final = offboard.run(app, tester)
    reports.offboarding(app, final)
    after = audit.run(app)
    reports.audit(app, after, "audit_report_after_offboarding")

    banner("6b. Three months later: the weekly audit catches drift and stale access")
    advance(24 * 95)
    app.store.upsert_account(b, recovery_email="monsoon.personal@gmail.example")   # reported by a person (simulated)
    app.log.write("drift.reported", msg="owner reports the recovery email was changed to a personal address (simulated)")
    later = audit.run(app)
    reports.audit(app, later, "audit_report_three_months_later")
    for f in later["findings"]:
        print(f"  [{f['severity']}] {f['detail']}  ({f['verification']}, task #{f['task']})")
    app.store.upsert_account(b, recovery_email=f"instagram@{cfg.company_domain}")  # fixed by the owner

    banner("7. Proxies on a neutral endpoint: health, pinning, rotation, failover, challenge")
    import httpx
    public = cfg.proxy_policy.get("neutral_endpoint", "")
    challenge_path = cfg.proxy_policy.get("challenge_path", "status/403")
    try:
        online = public.startswith("http") and httpx.get(public, timeout=5).status_code == 200
    except httpx.HTTPError:
        online = False
    ep = None
    if online:
        endpoint = public
        print(f"neutral endpoint: {public} (public). Challenge stand-in: {public.rsplit('/', 1)[0]}/{challenge_path}")
    else:
        ep = NeutralEndpoint(port=0)
        endpoint, challenge_path = f"http://127.0.0.1:{ep.port}/ip", "challenge"
        print(f"public endpoint unreachable; using the local neutral endpoint {endpoint}")
    app.log.write("proxy.endpoint", msg=f"neutral endpoint {endpoint} ({'public' if online else 'local fallback'})")
    creds = {n: (f"user-{n}", secrets.token_urlsafe(12)) for n in ("a", "b")}
    px = {n: TestProxy(f"proxy-{n}", *creds[n]) for n in ("a", "b")}
    for n in ("a", "b"):
        app.vault.put(ref(b, "proxy", n), px[n].url(*creds[n]), meta={"purpose": "local test proxy"})
    pool = ProxyPool(cfg.proxies, app.vault, app.log,
                     {**cfg.proxy_policy, "neutral_endpoint": endpoint, "max_session_requests": 3, "cooldown_s": 300})

    def on_challenge(account, detail):
        app.tasks.ensure(f"challenge:{account}", brand=b, kind="security_review",
                         title=f"Security challenge on {account}: review before any automation resumes",
                         instructions=f"{detail}. Resolve it yourself on the official site or app. "
                                      "Do not retry, rotate proxies or bypass it.",
                         owner=cfg.owner, escalate_to=cfg.escalate_to, due_in_hours=2)
    pool.on_challenge = on_challenge
    pool.check_all()

    def carried_by(r):
        hdrs = r["body"].get("headers", {})
        return hdrs.get("X-Test-Proxy") or r["body"].get("via") or r["proxy"]
    seen = [carried_by(pool.request("demo-session")) for _ in range(3)]
    print("3 requests in one session were carried by (as echoed by the endpoint):", set(seen))
    pool.maybe_rotate("demo-session")
    print("after the request budget, the next request is carried by:", carried_by(pool.request("demo-session")))
    px["b"].stop()
    try:
        r = pool.request_with_failover("demo-session")
        print("proxy-b stopped; failover served by:", carried_by(r))
    except ProxyDown as exc:
        print("failover failed:", exc)
    try:
        pool.request("challenge-session", challenge_path)
    except SecurityChallenge as exc:
        print("challenge -> paused:", exc)
    try:
        r = pool.request("challenge-session")
        print("ERROR: a paused account was allowed to reconnect via", r["proxy"])
    except SecurityChallenge as exc:
        print("further requests refused until a human clears it:", exc)
    proxy_status = pool.status()
    if ep:
        ep.stop()
    px["a"].stop()

    banner("7b. The vault: how credentials and backup codes are stored and read")
    from .vault import fingerprint
    for r in app.vault.refs():
        m = app.vault.metadata(r)
        value = app.vault.get(r)          # a real read: decrypts the value in memory only
        print(f"  {r:<48} v{m.get('version')}  {fingerprint(value)}  ({len(value)} chars, value never printed)")
    print("  the database, config and reports store only these vault:// references")

    banner("8. Reports and secret scan")
    for f in (reports.register(app), reports.tasks(app), reports.run_log(app)):
        print("wrote", f)
    scan = reports.secret_scan(app)
    print("secret scan:", scan)
    print(f"\nDone. Reports are in {state}/reports/. Inspect the result with:\n"
          f"  uv run python -m igops --state {state} status\n"
          f"  uv run python -m igops --state {state} vault verify")
    return {"outcome": outcome, "audit": audit_rep, "offboarding": final, "proxy": proxy_status, "scan": scan,
            "signup_tasks": signup_tasks_after}
