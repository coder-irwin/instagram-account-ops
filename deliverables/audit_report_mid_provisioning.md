# Audit report audit-20261020T151638

Ran 2026-10-20T15:16:38+00:00 in **simulation** mode. 9 checks, 4 findings. Schedule: weekly, Mondays 09:00 (see README for the cron line).

Verification labels: **integration-verified** = checked by code against a system of record; **human-confirmed** = rests on a completed task; **unverified** = no evidence yet. In simulation mode the Meta API is simulated and labelled so.

| Severity | Finding | Verified | Follow-up task |
|---|---|---|---|
| high | no backup codes in the vault | integration-verified | #6 |
| high | two-factor is not recorded as enabled | unverified | #7 |
| medium | setup incomplete (status created) | human-confirmed | #8 |
| low | last verified never | unverified | #9 |
