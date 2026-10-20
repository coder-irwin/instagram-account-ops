# Decision log

**Principle.** Automate the coordination, never the platform. People do every Instagram step without a supported integration. Secrets live only in the vault.

**Environment.** As GAJAN asked on 19 October, everything is simulated: an invented brand (Monsoon Threads), placeholder owners (Owner A, and Owner B leaving), a local encrypted vault, local test proxies, httpbin.org as the neutral endpoint, and a labelled mock of Meta's API. Assumptions are in the README.

## What I built

- **Resumable provisioning in 12 steps.** Human steps are keyed tasks with owner, deadline, reminders and escalation. Finished steps never rerun, and one signup task means no duplicate account.
- **An encrypted, versioned vault** with hidden entry, log redaction and a leak scan of every output.
- **A register and weekly audit** with labelled findings, and follow-ups that reopen or close themselves.
- **Offboarding:** remove roles, rotate the password, replace backup codes, revoke vault access, each reported as completed, pending or blocked.
- **A proxy pool:** health checks, timeouts, bounded retries, pinned sessions, rotation only between sessions, and a pause on any challenge.

## What I skipped, and why

- **Part D, the device fleet.** It's optional, and the brief rewards one complete path.
- **Mail and telephony integrations.** None were specified, so they are tracked human requests.
- **Role assignment by API.** Its permissions depend on GAJAN's Meta setup, so roles are human-granted for now.

## Boundaries, and where the brief needed care

- **Signup, verification, two-factor and profile edits stay human.** No supported API exists, and automating signup is prohibited.
- **A person checks handle availability.** Bulk-probing profile URLs is restricted automated access.
- **Proxies.** Rotating IPs against Instagram would look like evasion. Meta's official API needs no proxy, so Instagram is never reached through one. Rotation is shown only on a neutral endpoint, between sessions.
- **Challenges, blocks and rate limits pause and escalate.** They are never retried or rotated around, and a test enforces this.

## Where this breaks

- **Nothing has touched a real account.** The Meta client, vault adapters and proxy logic are proven against mocks and local stand-ins only.
- **Human-confirmed facts depend on honest evidence.** The audit can only re-check what an API exposes.

**Self-review.** I reviewed my own build like a strict assessor. It found 13 real defects, including a token leak in API errors and a lock two processes could both hold. All are fixed, each with a test. See [docs/ENGINEERING_REVIEW.md](docs/ENGINEERING_REVIEW.md).

## Next-day improvements

1. With real access: `setup`, `doctor`, `provision`, and check the Meta and vault adapters against the real services.
2. Assign and verify roles through the Business Management API.

## Tools used

Python, SQLite, httpx, cryptography, pytest, Ruff and mypy. An AI coding assistant (Claude Code) helped draft code and documents. The design, scope, boundaries and verification are mine.
