# Engineering review: how this was tested and what it found

After building the prototype I reviewed it as a strict assessor would, then verified every command and report by hand. I ran adversarial probes against the riskiest areas, fixed every real defect, and added a regression test for each fix. This file records that review.

## Evidence

| What | Result |
|---|---|
| Behaviour tests | 28, all passing, with no network needed |
| Line coverage | 93 percent of lines, measured with coverage.py |
| Real process kill | A separate process is killed with `os._exit(137)` right after a step commits. The restart resumes immediately, keeps the same password version, and still has exactly one signup task. |
| Concurrent runners | Two processes provision the same brand at the same moment, 5 rounds each. Result: 0 duplicate tasks. |
| Offboarding rerun | 3 reruns after completion: still 4 of 4 completed, the password version unchanged, 0 new tasks |
| Secret scan | Every vault value decrypted and searched for in every state file, including the SQLite database: 0 leaks |
| Platform facts | Checked against Meta's IG User reference. The fields we read exist, and profile updates and account creation are not supported, which is why those are human steps. |

## Defects found and fixed

| # | Defect | Risk | Fix | Test |
|---|---|---|---|---|
| 1 | A Meta API 4xx error raised an exception whose message contained the request URL, and Meta passes the token in that URL | Token in logs or console (secrets, 20 points) | Errors are built from the status and Meta's error text only, then redacted. `raise_for_status()` is never used. | `test_meta_errors_never_carry_the_token` |
| 2 | An unexpected exception inside a step crashed the run and left the step stuck as "running" | Unclean recovery (reliability, 25 points) | Any unexpected error marks the step failed with a redacted message, and the run stops cleanly | `test_unexpected_bug_fails_the_step_instead_of_crashing` |
| 3 | Every run used the lease holder name "cli", so two processes could both hold the lease | Duplicate provisioning | Holders are `host:pid:nonce` | `test_lease_blocks_live_holder_and_takes_over_dead_one` |
| 4 | After a kill, the lease blocked recovery for up to 15 minutes | Slow recovery | A lease held by a dead process on this host is taken over at once | `test_kill_minus_nine_mid_run_resumes_immediately` |
| 5 | The vault key was stored next to the vault file | Anyone with the folder could decrypt | The key lives in `~/.config/igops/keys` with permissions 600, or in `IGOPS_VAULT_KEY`. Old keys are migrated out automatically. | `test_vault_key_is_not_stored_in_the_state_folder` |
| 6 | The secret scan skipped the database | A leak could hide in task text | The scan reads every state file as bytes | `test_secret_scan_also_reads_the_database` |
| 7 | Rerunning offboarding re-promoted the staged password each time, and dropped completed actions from the report | Needless rotation and a misleading report | Promotion happens once, and revoked grants are kept as records | `test_offboarding_rerun_is_idempotent` |
| 8 | A security challenge paused the account, but the next request opened a fresh session | Automation could continue after a challenge (boundaries, 15 points) | A paused account refuses every request, including failover, until a person clears it | `test_challenge_pauses_and_does_not_rotate` |
| 9 | Proxy requests opened a new connection every time | Not a "consistent connection" during a session | One keep-alive client per session, closed when the session ends | Covered by the pinning and rotation tests |
| 10 | Rejecting secret evidence in `done` printed a raw Python traceback | Confusing for an operator | A one-line refusal and exit code 2 | Checked by hand |
| 11 | After rotation, the staged copy of the new password stayed in the vault | Needless duplicate secret | The staged copy is deleted once promoted | `test_offboarding_rerun_is_idempotent` |
| 12 | "Last verified never" was labelled integration-verified, and a person's confirmation was stated as fact | Overclaimed evidence (completion evidence, 10 points) | "never" is labelled unverified, and human-confirmed facts are labelled so | Checked by hand in the reports |
| 13 | The challenge pause lived only in memory, so a restart forgot it, and closing the task did nothing | Automation could resume after a challenge without a person (boundaries, 15 points) | The pause is the open security-review task, checked before every connection. It survives restarts and is lifted only when a person closes the task. | `test_challenge_pause_survives_restart_and_is_lifted_only_by_a_person` |

## What is still unproven

- **The Meta client, and the HashiCorp and 1Password adapters,** are verified against mocks only. GAJAN asked for a simulated environment.
- **Instagram steps are human by design,** so their correctness rests on the evidence a person records.
