"""igops command line.

uv run python -m igops demo                      full simulated walkthrough, writes state/demo/reports
uv run python -m igops status                    register, steps and open tasks
uv run python -m igops provision                 run or resume provisioning
uv run python -m igops tasks                     open human tasks
uv run python -m igops done ID key=value ...     complete a task with (non-secret) evidence
uv run python -m igops sweep                     send reminders, escalate overdue tasks
uv run python -m igops vault put REF --prompt    store a secret without echoing it
uv run python -m igops vault list                references and metadata only, never values
uv run python -m igops vault verify [REF]        decrypt and show a fingerprint, never the value
uv run python -m igops audit                     run the security audit now
uv run python -m igops schedule                  print the weekly cron line
uv run python -m igops offboard EMAIL            offboard a departing person
uv run python -m igops proxy-check               health-check the configured proxies
uv run python -m igops report                    write register, tasks, run log, secret scan
uv run python -m igops setup                     enter the real values; secrets go to the vault
uv run python -m igops doctor                    what still stands between this and a live run
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import audit, offboard, provision, reports
from .app import build
from .proxy import ProxyPool


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="igops")
    ap.add_argument("--config", default="config/brand.toml")
    ap.add_argument("--state", default="state")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo")
    sub.add_parser("status")
    sub.add_parser("provision")
    sub.add_parser("tasks")
    d = sub.add_parser("done")
    d.add_argument("id", type=int)
    d.add_argument("evidence", nargs="*")
    d.add_argument("--by", default=None)
    sub.add_parser("sweep")
    v = sub.add_parser("vault")
    v.add_argument("action", choices=["put", "list", "verify"])
    v.add_argument("ref", nargs="?")
    v.add_argument("value", nargs="?", help=argparse.SUPPRESS)  # accepted only to refuse it clearly
    v.add_argument("--prompt", action="store_true", help="read the value with hidden input")
    a = sub.add_parser("audit")
    a.add_argument("--scheduled", action="store_true")
    sub.add_parser("schedule")
    o = sub.add_parser("offboard")
    o.add_argument("email")
    sub.add_parser("proxy-check")
    sub.add_parser("report")
    sub.add_parser("setup")
    sub.add_parser("doctor")
    args = ap.parse_args(argv)

    if args.cmd == "demo":
        from . import demo

        res = demo.run(args.config, str(Path(args.state) / "demo"))
        return 0 if not res["scan"]["leaks"] else 1

    if args.cmd == "setup":
        from .golive import setup

        setup(args.config, args.state)
        return 0

    app = build(args.config, args.state)

    if args.cmd == "doctor":
        from .golive import doctor

        rows = doctor(app)
        for r in rows:
            print(
                f"{'OK  ' if r['ok'] else 'TODO'}  {r['check']:<34} {r['detail']}"
                + (f"\n      fix: {r['fix']}" if r["fix"] else "")
            )
        todo = sum(not r["ok"] for r in rows)
        ready = "Simulation ready to run." if app.cfg.simulation else "Ready to go live."
        print(f"\n{ready if not todo else f'{todo} item(s) left.'}")
        return 0 if not todo else 1

    if args.cmd == "status":
        acc = app.store.account(app.cfg.brand_key)
        if not acc:
            print(
                f"No account in '{args.state}' yet. Run `igops provision` to start, or inspect the demo "
                "with `igops --state state/demo status`."
            )
            return 0
        print(
            json.dumps(
                {
                    "account": {
                        k: acc[k]
                        for k in ("brand", "handle", "status", "two_factor_method", "last_verified_at", "blockers")
                    }
                    if acc
                    else None,
                    "steps": [(s["name"], s["status"]) for s in app.store.steps(app.cfg.brand_key)],
                },
                indent=2,
            )
        )
        args.cmd = "tasks"
    if args.cmd == "provision":
        print("outcome:", provision.run(app))
    if args.cmd == "tasks":
        for t in app.tasks.open_tasks():
            print(f"#{t['id']:<3} due {t['due_at'][:16]}  {t['owner']:<32} {t['title']}\n      {t['instructions']}")
    elif args.cmd == "done":
        bad = [e for e in args.evidence if "=" not in e]
        if bad:
            print(f"evidence must look like key=value, got: {' '.join(bad)}")
            return 2
        ev = dict(e.split("=", 1) for e in args.evidence)
        try:
            t = app.tasks.complete(args.id, ev, by=args.by or app.cfg.owner)
            print(f"task #{t['id']} done. Run `igops provision` to continue.")
        except (ValueError, KeyError) as exc:
            print(f"refused: {exc.args[0] if exc.args else exc}")
            return 2
    elif args.cmd == "sweep":
        print(app.tasks.sweep())
    elif args.cmd == "vault":
        if args.action == "list":
            for vref in app.vault.refs():
                print(vref, json.dumps(app.vault.metadata(vref)))
        elif args.action == "verify":
            from .vault import fingerprint

            for vref in [args.ref] if args.ref else app.vault.refs():
                secret = app.vault.get(vref)  # decrypts; proves the secret is readable without showing it
                version = app.vault.metadata(vref).get("version")
                print(f"{vref}  readable  v{version}  {fingerprint(secret)}  {len(secret)} chars")
        else:
            if args.value is not None:
                print(
                    "refused: secrets are never accepted as command arguments, because they would be saved "
                    "in shell history. Use: igops vault put vault://... --prompt"
                )
                return 2
            if not args.ref or not args.prompt:
                print("usage: vault put vault://... --prompt   (values are never accepted as arguments)")
                return 2
            value = getpass.getpass("value (hidden): ").strip()
            if not value:
                print("nothing stored")
                return 1
            app.vault.put(args.ref, value)
            print(f"stored {args.ref} (version {app.vault.metadata(args.ref)['version']})")
    elif args.cmd == "audit":
        rep = audit.run(app)
        print("wrote", reports.audit(app, rep))
    elif args.cmd == "schedule":
        print(audit.SCHEDULE_CRON.format(root=Path.cwd()))
    elif args.cmd == "offboard":
        rep = offboard.run(app, args.email)
        print("wrote", reports.offboarding(app, rep))
    elif args.cmd == "proxy-check":
        pool = ProxyPool(app.cfg.proxies, app.vault, app.log, app.cfg.proxy_policy)
        missing = [p["url_ref"] for p in app.cfg.proxies if not app.vault.exists(p["url_ref"])]
        if missing:
            print("BLOCKED: proxy credentials not in the vault:", ", ".join(missing))
            return 1
        print(pool.check_all())
    elif args.cmd == "report":
        for f in (reports.register(app), reports.tasks(app), reports.run_log(app)):
            print("wrote", f)
        print(reports.secret_scan(app))
    return 0


if __name__ == "__main__":
    sys.exit(main())
