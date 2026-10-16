# Screen recording script (2 to 3 minutes)

Record with Loom or QuickTime. **Before you start:** close chat apps and password managers, and check that no secret is on screen. Use `vault list`, which shows references, never `vault get`.

**0:00 to 0:20, the principle.** Show the README table. Say: "Code coordinates and records. People do every Instagram step that has no supported integration. Secrets live only in the vault."

**0:20 to 1:00, the live account.** Real account only, once GAJAN's access arrives:
- Run `igops status` to show the steps and open tasks.
- Show one task, such as two-factor, and complete it with `done`.
- Run `provision` and show that it continues.
- Show the account in the Instagram app and Business Suite, with the people and roles.

**1:00 to 1:30, interruption recovery.** Start `provision` and press Ctrl+C. Run it again: finished steps are skipped and `tasks` still shows one signup task. Or play this part from `igops demo`, scene 3.

**1:30 to 2:00, handoffs and escalation.** Show `sweep` output and the escalation line in `deliverables/run_log_redacted.jsonl`.

**2:00 to 2:30, audit, offboarding and proxies.** Open the audit report and point to the verification labels. Open the offboarding report and point to the completed, pending and blocked columns. Show the proxy challenge lines: paused, no rotation.

**2:30 to 3:00, honest blockers.** Show the register's blockers line, and say which access is still missing and the command that resumes once it arrives.
