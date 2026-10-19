# igops: semi-automated operations for one Instagram brand account

A prototype that provisions, secures, audits and offboards **one company-authorised Instagram brand account**. Code does the coordination and record-keeping. People do signup, verification and anything Instagram offers no supported integration for. Every handoff is a tracked task with an owner, a deadline, reminders and escalation, and the workflow resumes cleanly afterwards.

**Read [DECISION_LOG.md](DECISION_LOG.md) first.** The step-by-step guide for the real account is [LIVE_RUNBOOK.md](LIVE_RUNBOOK.md).

## Environment and assumptions

GAJAN asked for the whole environment to be simulated, in an email from Gagandeep Thind on 19 October 2026: no real Instagram account, no real company systems and no live proxies. Here is what stands in for each part:

| Brief asks for | What this submission uses |
|---|---|
| Throwaway test brand and brand kit | **Monsoon Threads**, an invented apparel brand. See [brand_kit/README.md](brand_kit/README.md). |
| Test company email and phone | `monsoonthreads.example`, a reserved domain that can't be real, and `+1 555 0142 001`, a reserved fictional number |
| Vault | A local encrypted file vault. The demo stores and reads the password, backup codes and proxy credentials, showing only versions and fingerprints. HashiCorp Vault and 1Password adapters exist for a real deployment. |
| Human task owners | **Owner A** owns the tasks, **Ops Lead** gets escalations, and **Editor C** is a normal team member |
| Departing team member | **Owner B (leaving)**, holding a designated temporary role, `insights_viewer`. The offboarding run targets them. |
| Meta business and Instagram | A clearly labelled mock of the official Graph API. Steps with no supported API are human tasks. |
| Approved proxies | Two local authenticated test proxies started by the demo. No live proxies. |
| Neutral endpoint | **http://httpbin.org/headers**, which echoes which proxy carried each request. **/status/403** stands in for a security challenge. If httpbin is unreachable, the demo uses a local endpoint and says so. |

**Other assumptions:**
- The account owner does every Instagram action by hand: signup, verification, two-factor, the business switch, profile edits and Business Suite roles.
- Simulated human completions in the demo are labelled "(simulated)".
- Every "integration-verified" result that came from the mock is labelled "(simulated)".

## If a real environment is provided later

Every stand-in can be replaced with real access in about ten minutes:

```bash
uv run python -m igops setup      # real values; secrets typed hidden, straight into the vault
uv run python -m igops doctor     # lists anything still missing, each with its fix
uv run python -m igops provision  # the live run: stops at each human step, resumes after
```

`setup` supports a local encrypted vault, HashiCorp Vault and 1Password. A test rehearses this path end to end against fake versions of GAJAN's services, using the real Meta client and real proxy checks.

## What is real and what is simulated

| Part | Status |
|---|---|
| Workflow engine, task tracking, reminders, escalation, crash recovery | Real, runs as is |
| Vault: encrypted, versioned, values never printed | Real local vault, plus HashiCorp Vault and 1Password adapters tested against fakes |
| Account register, audit, offboarding, reports, secret scan | Real |
| Proxy health checks, timeouts, retries, pinned sessions, rotation, failover, challenge pause | Real, run against a local neutral endpoint and two local test proxies |
| Meta Graph API verification of the Instagram profile | Real client written. **Simulated in the demo**, because no Meta token for GAJAN's test business has been provided yet. Everything simulated is labelled "simulated" in every report. |
| Instagram signup, two-factor, profile edits, business conversion, Business Suite roles | **Human steps by design.** Instagram has no supported API for them. |

## Run it

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
uv run pytest -q                     # 20 behaviour tests
uv run python -m igops demo          # full simulated walkthrough, about 5 seconds
```

The demo writes every deliverable report to `state/demo/reports/`. A copy from the last run is in [deliverables/](deliverables/).

## What the demo shows

1. **Provisioning pauses at each human handoff.** It covers handle availability, the email alias, the phone number, signup, two-factor, business linking, the profile and role grants. A simulated owner completes each task and the workflow continues.
2. **A reminder, then one escalation,** when the signup task goes overdue. Sweeping again never escalates twice.
3. **A crash mid-run.** The rerun resumes at the interrupted step. There is still exactly one signup task, so no duplicate account is possible. The generated password is not regenerated.
4. **A transient Meta API error** is retried with backoff, then verification succeeds.
5. **Audits:**
   - Mid-provisioning, it flags missing backup codes, two-factor off and incomplete setup.
   - After setup, it finds nothing, and earlier follow-ups close themselves.
   - Three months later, it flags a personal recovery email and stale access.
6. **Offboarding the temporary tester.** The first pass leaves 4 actions pending. Once people act, all 4 are completed: role removed, password rotated, backup codes replaced, and vault access revoked.
7. **Proxies:**
   - Health checks pass.
   - A session stays on one proxy.
   - Rotation happens only between sessions.
   - When a proxy is killed, the session ends and fails over to a new one.
   - A security challenge pauses the account, opens a human task, and refuses to reconnect, even through failover.
8. **A secret scan.** Every vault value is decrypted and searched for in every output file. The result is 0 leaks.

## Commands for the real account

```bash
uv run python -m igops status                         # register, steps, open tasks
uv run python -m igops provision                      # run or resume; safe to run repeatedly
uv run python -m igops tasks                          # what people need to do
uv run python -m igops done 7 ig_user_id=1784...      # close a task with non-secret evidence
uv run python -m igops sweep                          # reminders and escalation (cron every 15 min)
uv run python -m igops vault put vault://... --prompt # store a secret with hidden input
uv run python -m igops vault list                     # references and metadata, never values
uv run python -m igops vault verify                   # decrypt each secret, show only a fingerprint
uv run python -m igops audit                          # run the audit now
uv run python -m igops schedule                       # prints the weekly cron line
uv run python -m igops offboard person@company        # offboard someone, rerun to continue
uv run python -m igops proxy-check                    # health-check the configured proxies
uv run python -m igops report                         # register, tasks, run log, secret scan
```

Set `mode = "live"` in `config/brand.toml` to use the real Meta API client instead of the simulation.

## Configuration

`config/brand.toml` holds the brand, people and their least-privilege roles, company contacts, proxies and policy. It contains **no secrets**, only `vault://` references. Values marked `CHANGE` come from GAJAN's test environment. The loader refuses:
- a bio over 150 characters
- a non-company email for a team member
- unknown roles
- more than two admins
- a proxy written as a literal URL instead of a vault reference

## How secrets are handled

- **Secrets live only in the vault.** That covers the password, backup codes, proxy URLs, the Meta token and SMTP credentials. The database, config and reports hold only `vault://` references.
- **Secrets are entered with `vault put --prompt`.** Input is hidden, and the command refuses values passed as arguments, which would land in shell history.
- **Task evidence can't carry secrets.** Completing a task with a field named like `password`, `code`, `secret` or `token` is rejected.
- **Every log line is redacted before it is written.** Any value read from the vault is registered with the redactor, and URL credentials, tokens and authorization headers are masked by pattern.
- **The report step finishes with a secret scan** that checks all outputs against the decrypted vault contents.

## Reliability

- **Steps are idempotent and recorded in SQLite.** Finished steps never rerun, and a crash leaves the step resumable.
- **Tasks are keyed by the real-world need,** such as `signup:<brand>`. Asking twice returns the same task, so a resume cannot request a second account.
- **A lease stops two runners** from provisioning the same account at once.
- **Transient errors get bounded retries** with exponential backoff and jitter. Hard errors stop the step and are recorded as failed or blocked, never silently skipped.
- **Missing access is a blocker, not a crash.** It is written to the register with the exact command that resumes once it is cleared.

## Limitations

- **The Meta API path is unverified against a real account,** because GAJAN's test business access hasn't been provided. The client follows the documented Graph API fields. Role assignment through the Business Management API is left as a human task until permissions are confirmed.
- **Email-alias and phone reservation are human requests.** No mail or telephony integration was specified.
- **Notifications go to the run log,** and to email once SMTP credentials are in the vault. There is no Slack integration.
- **Part D, the device fleet, was not built.** It is optional, and the brief rewards one complete path over breadth.
- **The HashiCorp and 1Password adapters are tested against fakes only.** Check them once against the real vault.

## Layout

```
igops/core.py        clock, redaction, redacted run log
igops/vault.py       local encrypted vault, HashiCorp and 1Password adapters
igops/golive.py      `setup` wizard and `doctor` readiness check
igops/store.py       SQLite: register, people, roles, tasks, steps, findings, lease
igops/tasks.py       human tasks: idempotent creation, reminders, escalation, notifier
igops/workflow.py    resumable step runner with bounded retries
igops/provision.py   the provisioning workflow (12 steps)
igops/audit.py       weekly audit with verification labels and follow-ups
igops/offboard.py    offboarding across accounts: completed, pending, blocked
igops/proxy.py       proxy pool: health, pinning, rotation, failover, challenge pause
igops/neutral.py     local neutral endpoint and test proxies
igops/providers/meta.py   official Graph API client and the labelled simulation
igops/reports.py     register, audit, offboarding, tasks, run log, secret scan
igops/demo.py        the scripted end-to-end walkthrough
```
