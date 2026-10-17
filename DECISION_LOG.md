# Decision log

**Principle.** Automate the coordination, never the platform. People do every Instagram step without a supported integration. Secrets live only in the vault.

**Access.** GAJAN's test environment was requested on 16 and 17 October but had not arrived by submission. Live mode records the blockers, and `setup`, `doctor` and `provision` resume once it arrives. I did not create my own Instagram account, because it wouldn't be company-authorised.

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

- **No real account yet.** The Meta API, vault adapters and proxies are tested against fakes and local stand-ins only.
- **Human-confirmed facts depend on honest evidence.** A killed process holds its run lease for up to 15 minutes.

## Next-day improvements

1. Connect the real vault and Meta token, then run and record the live path.
2. Assign and verify roles through the Business Management API.

## AI tools used

**Claude Code**, Anthropic's coding agent, wrote the code, tests and first drafts, and caught bugs in its own work, such as a paused account that could reconnect after a challenge. **I** set the scope, boundaries and build-versus-skip decisions.
