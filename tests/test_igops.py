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
    assert app.store.acquire("provision:northwind-test", "runner-1")
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
    text = CFG.read_text().replace('bio = "Test brand', 'bio = "' + "x" * 160 + ' Test brand')
    p = tmp_path / "c.toml"; p.write_text(text)
    with pytest.raises(ConfigError):
        load(p)
    p.write_text(CFG.read_text().replace('url_ref = "vault://northwind-test/proxy/a"', 'url_ref = "http://u:p@h:1"'))
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
                  {"neutral_endpoint": f"http://127.0.0.1:{ep.port}/ip", "max_session_requests": 2,
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
