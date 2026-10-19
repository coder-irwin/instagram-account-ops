# Screen recording script (2 to 3 minutes)

Record with Loom or QuickTime in the project folder. **Before you start:** close chat apps and password managers. Never run anything that prints a secret. `vault list` and `vault verify` are safe.

1. **0:00 to 0:20, the setup.** Show the README section "Environment and assumptions". Say: "As GAJAN asked, the environment is simulated: an invented brand, placeholder owners, a local vault, local proxies and httpbin as the neutral endpoint."
2. **0:20 to 1:20, the walkthrough.** Run `uv run python -m igops demo` and scroll through:
   - **Handoffs:** each step stops at a human task with an owner and deadline.
   - **Reminder, then escalation:** the signup task goes overdue.
   - **Crash and resume:** one signup task before, one after, so no duplicate account.
   - **Proxies:** pinned, rotated between sessions, failover when proxy-b dies, and the 403 challenge pausing with no rotation.
3. **1:20 to 1:50, the vault.** Point at scene 7b: references, versions and fingerprints, never values. Run `uv run python -m igops --state state/demo vault verify`, then show the secret scan line with 0 leaks.
4. **1:50 to 2:30, the reports.** Open `deliverables/`:
   - **The account register:** masked contacts and vault references.
   - **The audit, three months later:** the verification labels on each finding.
   - **The offboarding report for Owner B:** 4 actions completed.
5. **2:30 to 3:00, the honest limits.** Show "Where this breaks" in the decision log. Say: "Instagram steps stay human by design. The Meta API is mocked and labelled. With real access it's setup, doctor, provision."
