"""Behaviour tests for the graded areas: reliability, secrets, handoffs, proxy judgment."""
import json
import secrets as pysecrets
from pathlib import Path

import pytest

from igops import audit, core, offboard, provision, reports
from igops.app import build
from igops.config import ConfigError, load
from igops.neutral import NeutralEndpoint, TestProxy
from igops.providers.meta import TransientError
from igops.proxy import ProxyDown, ProxyPool, SecurityChallenge
from igops.vault import LocalVault, VaultError, ref
from igops.workflow import Done, Interrupted, run_steps

CFG = Path(__file__).resolve().parent.parent / "config" / "brand.toml"


@pytest.fixture(autouse=True)
def _keys_outside_home(tmp_path_factory, monkeypatch):
    # vault keys normally live in ~/.config/igops/keys; keep test keys out of the real home folder
    monkeypatch.setenv("IGOPS_KEY_DIR", str(tmp_path_factory.mktemp("keys")))


@pytest.fixture
def app(tmp_path):
    from datetime import timedelta
    core._offset = timedelta(0)          # each test starts at real time
    return build(CFG, tmp_path, echo=False)


def complete(app, prefix, **ev):
    t = next(t for t in app.tasks.open_tasks() if t["key"].startswith(prefix))
    app.tasks.complete(t["id"], ev, by="tester")


def drive_to_signup(app):
    b = app.cfg.brand_key
    provision.run(app)
    complete(app, "handle:", handle=app.store.account(b)["handle_candidates"][0])
    provision.run(app)
    complete(app, "email:", alias=f"instagram@{app.cfg.company_domain}")
    provision.run(app)
    complete(app, "phone:", phone=app.cfg.company_phones[0])
    provision.run(app)


# ---------------------------------------------------------------- reliability

def test_rerunning_never_duplicates_tasks(app):
    drive_to_signup(app)
    before = len(app.tasks.all())
    for _ in range(3):
        provision.run(app)
    assert len(app.tasks.all()) == before
    assert sum(t["key"].startswith("signup:") for t in app.tasks.all()) == 1


def test_crash_mid_run_resumes_without_redoing_finished_steps(app):
    drive_to_signup(app)
    pw_ref = app.store.account(app.cfg.brand_key)["vault_refs"]["password"]
    pw_version = app.vault.metadata(pw_ref)["version"]
    complete(app, "signup:", handle=app.store.account(app.cfg.brand_key)["handle"], created="new")
    calls = []

    def boom():
        calls.append(1)
        raise Interrupted("killed")
    with pytest.raises(Interrupted):
        run_steps(app.store, app.log, app.cfg.brand_key,
                  [(n, f) for n, f in provision.steps(app) if n != "two_factor"][:7] + [("two_factor", boom)])
    assert provision.run(app) == "waiting_human"              # resumes at two_factor
    assert app.vault.metadata(pw_ref)["version"] == pw_version  # password was not regenerated
    assert app.store.step(app.cfg.brand_key, "human_signup")["status"] == "done"


def test_transient_errors_are_retried_then_give_up(app):
    attempts = []

    def flaky():
        attempts.append(1)
        raise TransientError("503")
    out = run_steps(app.store, app.log, "x", [("flaky", flaky)], max_retries=3, sleep=lambda s: None)
    assert out == "failed" and len(attempts) == 3


def test_lease_blocks_a_second_concurrent_run(app):
    assert app.store.acquire(f"provision:{app.cfg.brand_key}", "runner-1")
    assert provision.run(app, holder="runner-2") == "locked"


def test_non_company_phone_is_blocked(app):
    b = app.cfg.brand_key
    provision.run(app)
    complete(app, "handle:", handle=app.store.account(b)["handle_candidates"][0])
    provision.run(app)
    complete(app, "email:", alias=f"instagram@{app.cfg.company_domain}")
    provision.run(app)
    complete(app, "phone:", phone="+91 98765 43210")
    assert provision.run(app) == "blocked"
    assert any("not on the company-controlled list" in x["text"] for x in app.store.account(b)["blockers"])


# ---------------------------------------------------------------- human handoffs

def test_reminder_then_single_escalation(app):
    drive_to_signup(app)
    core.advance(app.cfg.signup_due_hours - 2)
    assert app.tasks.sweep()["reminded"]
    core.advance(3)
    assert app.tasks.sweep()["escalated"]
    assert app.tasks.sweep()["escalated"] == []      # never escalates the same task twice


def test_evidence_with_secret_fields_is_rejected(app):
    drive_to_signup(app)
    t = app.tasks.open_tasks()[0]
    with pytest.raises(ValueError):
        app.tasks.complete(t["id"], {"backup_codes": "1234 5678"}, by="someone")


# ---------------------------------------------------------------- secrets

def test_vault_values_never_reach_logs_or_reports(app):
    drive_to_signup(app)
    pw = app.vault.get(app.store.account(app.cfg.brand_key)["vault_refs"]["password"])
    app.log.write("oops", msg=f"debug password is {pw}")       # a careless log line
    reports.register(app); reports.tasks(app); reports.run_log(app)
    for f in app.state_dir.rglob("*"):
        if f.is_file() and f.suffix in (".md", ".json", ".jsonl"):
            assert pw not in f.read_text()
    assert reports.secret_scan(app)["leaks"] == []


def test_vault_rejects_wrong_key_and_keeps_history(tmp_path):
    v = LocalVault(tmp_path / "v.enc")
    v.put("vault://a/b", "first")
    v.put("vault://a/b", "second")
    assert v.metadata("vault://a/b")["version"] == 2 and v.get("vault://a/b") == "second"
    with pytest.raises(VaultError):
        LocalVault(tmp_path / "v.enc", key=b"A" * 43 + b"=")


def test_redaction_patterns():
    s = core.redact("connect http://bob:hunter2@10.0.0.1:8080 with access_token=EAABBBBBBBBBBBBBBBBBBBBBBBBB")
    assert "hunter2" not in s and "EAAB" not in s


def test_config_rejects_long_bio_and_literal_proxy(tmp_path):
    text = CFG.read_text().replace('bio = "Handloom', 'bio = "' + "x" * 160 + ' Handloom')
    p = tmp_path / "c.toml"; p.write_text(text)
    with pytest.raises(ConfigError):
        load(p)
    p.write_text(CFG.read_text().replace('url_ref = "vault://monsoon-threads/proxy/a"', 'url_ref = "http://u:p@h:1"'))
    with pytest.raises(ConfigError):
        load(p)


# ---------------------------------------------------------------- audit and offboarding

def test_audit_labels_and_followups_are_idempotent(app):
    drive_to_signup(app)
    r1 = audit.run(app)
    r2 = audit.run(app)
    assert {f["verification"].split(" ")[0] for f in r1["findings"]} <= {"integration-verified", "human-confirmed", "unverified"}
    assert [f["task"] for f in r1["findings"]] == [f["task"] for f in r2["findings"]]


def test_offboarding_reports_pending_then_completed(app):
    from igops import demo
    res = demo.run(CFG, str(app.state_dir / "full"))
    assert res["outcome"] == "complete"
    assert res["offboarding"]["summary"] == {"completed": 4, "pending": 0, "blocked": 0}
    assert res["signup_tasks"] == 1
    assert res["scan"]["leaks"] == []


# ---------------------------------------------------------------- proxies

@pytest.fixture
def pool(app):
    ep = NeutralEndpoint(port=0)
    creds = {n: (f"u{n}", pysecrets.token_urlsafe(8)) for n in "ab"}
    px = {n: TestProxy(f"proxy-{n}", *creds[n]) for n in "ab"}
    for n in "ab":
        app.vault.put(ref(app.cfg.brand_key, "proxy", n), px[n].url(*creds[n]))
    p = ProxyPool(app.cfg.proxies, app.vault, app.log,
                  {"neutral_endpoint": f"http://127.0.0.1:{ep.port}/ip", "max_session_requests": 2,  # local: tests stay offline
                   "max_retries": 2, "cooldown_s": 300})
    yield p, px
    ep.stop()
    for x in px.values():
        try:
            x.stop()
        except Exception:
            pass


def test_session_is_pinned_and_rotates_only_between_sessions(pool):
    p, _ = pool
    assert {p.request("s")["proxy"] for _ in range(2)} == {"proxy-a"}
    assert p.maybe_rotate("s") and p.request("s")["proxy"] == "proxy-b"


def test_failover_after_proxy_dies(pool):
    p, px = pool
    p.request("s")
    px["a"].stop()
    assert p.request_with_failover("s")["proxy"] == "proxy-b"


def test_challenge_pauses_and_does_not_rotate(pool):
    p, _ = pool
    seen = []
    p.on_challenge = lambda acc, d: seen.append(acc)
    with pytest.raises(SecurityChallenge):
        p.request("s", "challenge")
    with pytest.raises(SecurityChallenge):
        p.request_with_failover("s")          # failover must not be a way around a challenge
    assert seen == ["s"]


def test_proxy_credentials_never_logged(pool, app):
    p, _ = pool
    p.check_all(); p.request("s")
    log = (app.state_dir / "run_log.jsonl").read_text()
    for n in "ab":
        url = app.vault.get(ref(app.cfg.brand_key, "proxy", n))
        assert url not in log and url.split("@")[0].split(":")[-1] not in log


# ---------------------------------------------------------------- going live

def test_hashicorp_adapter_against_fake_server():
    import httpx
    from igops.vault import HashiCorpVault
    store, meta = {}, {}

    def handler(req: httpx.Request):
        path = req.url.path
        kind, key = ("data", path.split("/data/", 1)[1]) if "/data/" in path else ("meta", path.split("/metadata/", 1)[1])
        assert req.headers["X-Vault-Token"] == "t0k3n-xyz"
        if req.method == "POST" and kind == "data":
            store[key] = json.loads(req.content)["data"]; meta.setdefault(key, {"v": 0}); meta[key]["v"] += 1
            return httpx.Response(200, json={})
        if req.method == "POST":
            meta[key]["cm"] = json.loads(req.content)["custom_metadata"]; return httpx.Response(204)
        if key not in store:
            return httpx.Response(404)
        if kind == "data":
            return httpx.Response(200, json={"data": {"data": store[key]}})
        return httpx.Response(200, json={"data": {"current_version": meta[key]["v"], "custom_metadata": meta[key].get("cm")}})
    v = HashiCorpVault("http://vault.test", token="t0k3n-xyz",
                       client=httpx.Client(base_url="http://vault.test", transport=httpx.MockTransport(handler),
                                           headers={"X-Vault-Token": "t0k3n-xyz"}))
    v.put("vault://b/x", "s3cret", meta={"purpose": "test"})
    v.put("vault://b/x", "s3cret2")
    assert v.get("vault://b/x") == "s3cret2" and v.exists("vault://b/x") and not v.exists("vault://b/y")
    assert v.metadata("vault://b/x")["version"] == 2 and v.metadata("vault://b/x")["purpose"] == "test"
    assert "s3cret2" not in core.redact("value s3cret2")


def test_onepassword_adapter_never_puts_secrets_on_the_command_line():
    from types import SimpleNamespace
    from igops.vault import OnePasswordVault
    items, argv_seen = {}, []

    def runner(args, input=None):
        argv_seen.append(" ".join(args))
        if args[:2] == ["item", "create"]:
            t = json.loads(input); items[t["title"]] = t
            return SimpleNamespace(returncode=0, stdout="{}")
        if args[:2] == ["item", "get"]:
            it = items.get(args[2])
            return SimpleNamespace(returncode=0 if it else 1, stdout=json.dumps(it or {}))
        if args[:2] == ["item", "delete"]:
            items.pop(args[2], None); return SimpleNamespace(returncode=0, stdout="")
        if args[0] == "read":
            title = args[1][len("op://V/"):].rsplit("/password", 1)[0]
            return SimpleNamespace(returncode=0, stdout=items[title]["fields"][0]["value"] + "\n")
    v = OnePasswordVault("V", runner=runner)
    v.put("vault://b/pw", "hunter2!!", meta={"purpose": "test"})
    assert v.get("vault://b/pw") == "hunter2!!" and v.metadata("vault://b/pw")["version"] == 1
    assert not any("hunter2" in a for a in argv_seen)


def test_go_live_rehearsal_setup_doctor_and_live_provision(tmp_path):
    """The real code paths with fake GAJAN services: setup -> doctor -> live provisioning."""
    import httpx
    from datetime import timedelta
    from igops import golive
    from igops.providers.meta import MetaGraph
    core._offset = timedelta(0)
    cfg_path = tmp_path / "brand.toml"; cfg_path.write_text(CFG.read_text())
    logo = tmp_path / "logo.png"; logo.write_bytes(b"png")
    ep = NeutralEndpoint(port=0)
    creds = {n: (f"u{n}", pysecrets.token_urlsafe(8)) for n in "ab"}
    px = {n: TestProxy(f"proxy-{n}", *creds[n]) for n in "ab"}
    answers = iter(["acme-live", "Acme Live", "acme.test", "https://acme.test", "Shopping & retail",
                    "Acme test account for operations prototype.", str(logo), "owner@acme.test", "lead@acme.test",
                    "+1 555 0100 999", "998877", f"http://127.0.0.1:{ep.port}/ip", "local",
                    "owner@acme.test", "editor@acme.test", "temp@acme.test"])
    secret_answers = iter(["EAAtesttokenvalue1234567890abcdef", px["a"].url(*creds["a"]), px["b"].url(*creds["b"]), ""])
    golive.setup(cfg_path, tmp_path, ask=lambda q: next(answers), ask_secret=lambda q: next(secret_answers))

    profile = {"username": None, "name": "Acme Live", "biography": "Acme test account for operations prototype.",
               "website": "https://acme.test"}

    def graph(req: httpx.Request):
        assert req.url.params["access_token"] == "EAAtesttokenvalue1234567890abcdef"
        if req.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "1", "name": "acme system user"})
        return httpx.Response(200, json={**profile, "id": req.url.path.rsplit("/", 1)[1]})
    app = build(cfg_path, tmp_path, echo=False)
    app.meta = MetaGraph(app.vault, app.cfg.meta_token_ref, app.log, client=httpx.Client(transport=httpx.MockTransport(graph)))
    rows = golive.doctor(app)
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]

    b = app.cfg.brand_key
    ev = {"handle:": lambda: {"handle": app.store.account(b)["handle_candidates"][0]},
          "email:": lambda: {"alias": "instagram@acme.test"}, "phone:": lambda: {"phone": "+1 555 0100 999"},
          "signup:": lambda: {"handle": app.store.account(b)["handle"], "created": "new"},
          "2fa:": lambda: {"method": "authenticator_app"}, "business:": lambda: {"ig_user_id": "17841499"},
          "profile:": lambda: {"done": "yes"}}
    for _ in range(20):
        out = provision.run(app)
        if out != "waiting_human":
            break
        for t in app.tasks.open_tasks():
            if t["key"].startswith("2fa-codes"):
                app.vault.put(app.store.account(b)["vault_refs"]["backup_codes"], "12345678 87654321")
                app.tasks.complete(t["id"], {"stored": "yes"}, by="t")
            elif t["key"].startswith("role-"):
                app.tasks.complete(t["id"], {"granted": "yes"}, by="t")
            else:
                k = next((k for k in ev if t["key"].startswith(k)), None)
                if k:
                    if k == "business:":
                        profile["username"] = app.store.account(b)["handle"]
                    app.tasks.complete(t["id"], ev[k](), by="t")
    acc = app.store.account(b)
    assert out == "complete" and acc["status"] == "active"
    assert acc["last_verified_how"] == "integration-verified"            # the real client, not the simulation
    assert reports.secret_scan(app)["leaks"] == []
    assert "EAAtesttoken" not in cfg_path.read_text()
    ep.stop(); [x.stop() for x in px.values()]


# ---------------------------------------------------------------- findings from the review

def test_meta_errors_never_carry_the_token(app):
    import httpx, traceback
    from igops.providers.meta import MetaGraph, MetaApiError
    token = "EAAleakcheck" + "y" * 30
    app.vault.put(app.cfg.meta_token_ref, token)
    for status in (400, 404, 422):
        mg = MetaGraph(app.vault, app.cfg.meta_token_ref, app.log,
                       client=httpx.Client(transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s, json={"error": {"message": "x"}}))))
        with pytest.raises(MetaApiError) as ei:
            mg.instagram_profile("1")
        assert token not in str(ei.value) and token not in "".join(traceback.format_exception(ei.value))


def test_unexpected_bug_fails_the_step_instead_of_crashing(app):
    def bug():
        raise ValueError("boom")
    assert run_steps(app.store, app.log, "x", [("bug", bug)]) == "failed"
    assert app.store.step("x", "bug")["status"] == "failed"


def test_lease_blocks_live_holder_and_takes_over_dead_one(app):
    import socket
    from igops.store import new_holder
    assert app.store.acquire("L", new_holder())
    assert not app.store.acquire("L", new_holder())
    assert app.store.acquire("L2", f"{socket.gethostname()}:999999:dead0000")
    assert app.store.acquire("L2", new_holder())


def test_vault_key_is_not_stored_in_the_state_folder(app):
    assert not any(p.suffix == ".key" for p in app.state_dir.rglob("*"))


def test_secret_scan_also_reads_the_database(app):
    drive_to_signup(app)
    pw = app.vault.get(app.store.account(app.cfg.brand_key)["vault_refs"]["password"])
    app.store.db.execute("UPDATE tasks SET instructions=? WHERE id=1", (f"leaked {pw}",))
    app.store.db.execute("PRAGMA wal_checkpoint(FULL)")
    assert reports.secret_scan(app)["leaks"]


def test_kill_minus_nine_mid_run_resumes_immediately(tmp_path):
    """A real separate process is killed with os._exit right after a step commits."""
    import os, subprocess, sys, textwrap
    script = textwrap.dedent(f"""
        from igops.app import build
        from igops import provision
        app = build({str(CFG)!r}, {str(tmp_path)!r}, echo=False)
        b = app.cfg.brand_key
        provision.run(app)
        for key, ev in (("handle:", {{"handle": app.store.account(b)["handle_candidates"][0]}}),
                        ("email:", {{"alias": "instagram@" + app.cfg.company_domain}}),
                        ("phone:", {{"phone": app.cfg.company_phones[0]}})):
            t = next(t for t in app.tasks.open_tasks() if t["key"].startswith(key))
            app.tasks.complete(t["id"], ev, by="t")
            provision.run(app)
    """)
    env = {**os.environ, "IGOPS_KILL_AFTER_STEP": "credentials", "PYTHONPATH": str(CFG.parent.parent)}
    p = subprocess.run([sys.executable, "-c", script], env=env, cwd=CFG.parent.parent, capture_output=True, text=True)
    assert p.returncode == 137, p.stderr[-500:]                     # it really died mid-run
    app = build(CFG, tmp_path, echo=False)
    pw_ref = app.store.account(app.cfg.brand_key)["vault_refs"]["password"]
    version = app.vault.metadata(pw_ref)["version"]
    assert provision.run(app) == "waiting_human"                    # lease of the dead process taken over
    assert app.store.step(app.cfg.brand_key, "credentials")["status"] == "done"
    assert app.vault.metadata(pw_ref)["version"] == version          # password not regenerated
    assert sum(t["key"].startswith("signup:") for t in app.tasks.all()) == 1


def test_offboarding_rerun_is_idempotent(tmp_path):
    import contextlib, io
    from igops import demo
    with contextlib.redirect_stdout(io.StringIO()):
        demo.run(CFG, str(tmp_path / "d"))
    app = build(CFG, tmp_path / "d", echo=False)
    ref_ = app.store.account(app.cfg.brand_key)["vault_refs"]["password"]
    v0, n0 = app.vault.metadata(ref_)["version"], len(app.tasks.all())
    for _ in range(3):
        rep = offboard.run(app, next(p.email for p in app.cfg.people if p.test_member))
    assert rep["summary"] == {"completed": 4, "pending": 0, "blocked": 0}
    assert app.vault.metadata(ref_)["version"] == v0 and len(app.tasks.all()) == n0
    assert not app.vault.exists(ref_ + ".next")          # staged copy deleted after promotion


def test_challenge_pause_survives_restart_and_is_lifted_only_by_a_person(pool, app):
    p, _ = pool
    p.on_challenge = lambda acc, d: app.tasks.ensure(f"challenge:{acc}", brand="b", kind="security_review",
                                                     title="review", instructions="x", owner="o", escalate_to="e",
                                                     due_in_hours=2)
    check = lambda acc: bool((t := app.tasks.get(f"challenge:{acc}")) and t["status"] == "open")
    p.paused_check = check
    with pytest.raises(SecurityChallenge):
        p.request("s", "challenge")
    fresh = ProxyPool(app.cfg.proxies, app.vault, app.log, p.p)      # simulated restart
    fresh.paused_check = check
    with pytest.raises(SecurityChallenge):
        fresh.request("s")
    app.tasks.complete(app.tasks.get("challenge:s")["id"], {"resolved": "yes"}, by="person")
    assert fresh.request("s")["status"] == 200
