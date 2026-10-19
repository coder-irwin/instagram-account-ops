# Audit report audit-20261020T130351

Ran 2026-10-20T13:03:51+00:00 in **simulation** mode. 9 checks, 2 findings. Schedule: weekly, Mondays 09:00 (see README for the cron line).

Verification labels: **integration-verified** = checked by code against a system of record; **human-confirmed** = rests on a completed task; **unverified** = no evidence yet. In simulation mode the Meta API is simulated and labelled so.

| Severity | Finding | Verified | Follow-up task |
|---|---|---|---|
| high | backup codes were exposed and not yet replaced | integration-verified | #19 |
| high | role removal for owner.b@monsoonthreads.example not confirmed yet | unverified | #20 |
