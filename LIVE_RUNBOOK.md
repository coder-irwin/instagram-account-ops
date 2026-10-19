# Live runbook: if a real environment is provided later

**Not needed for this submission.** GAJAN asked for a fully simulated environment on 19 October. This runbook shows how the same code would run against a real throwaway brand on real Instagram. Each step says who does it. **Never paste a password, backup code, token or proxy URL into chat, email, a task or a terminal argument.** Use `vault put --prompt`.

## 0. Access a real run would need

| Needed | Used for | Where it goes |
|---|---|---|
| Test brand kit: name, logo, bio, website, category | Profile | `config/brand.toml`, `brand_kit/` |
| Test company email domain and mailbox access | Recovery email alias | `config/brand.toml` |
| Test company phone number | Recovery phone and verification | `config/brand.toml` under `company_contacts.phones` |
| Vault access | All secrets | Adapter in `igops/vault.py`, or the local vault meanwhile |
| Separate Meta business id and a system-user token | Linking, people, API verification | `config/brand.toml` and the vault at `meta/system_user_token` |
| Approved proxies and a neutral test endpoint | Part C demonstration | The vault at `proxy/a` and `proxy/b`, plus `proxy_policy.neutral_endpoint` |
| Names and emails for owner, escalation and the temporary tester | Tasks and offboarding | `config/brand.toml` |

Until each one arrives, the matching step shows as **blocked** on the register, with the command that resumes it.

## 1. Configure (operator, about 10 minutes)

```bash
uv sync
uv run python -m igops setup     # answer each question; secrets are hidden and go to the vault
uv run python -m igops doctor    # repeat until it says "Ready to go live."
```

`setup` keeps a backup of the old config at `config/brand.toml.bak`. For HashiCorp Vault, export `VAULT_TOKEN` first. For 1Password, export `OP_SERVICE_ACCOUNT_TOKEN`.

## 2. Provision (operator plus task owner)

Run `uv run python -m igops provision`. It stops at each human step and prints the task. Do the task, close it with `done`, and run `provision` again. Running it repeatedly is always safe.

| Step | Who | What to do | Close with |
|---|---|---|---|
| Handle | Owner | Open instagram.com/<handle> for each suggestion, one by one. Take the first that shows "page isn't available". Don't use bulk-check tools. | `done ID handle=<chosen>` |
| Email alias | IT or owner | Create `instagram@<domain>`, routed to the owner | `done ID alias=instagram@<domain>` |
| Phone | IT or owner | Reserve the company test number for this brand | `done ID phone=<number>` |
| Signup | Owner | Search for the handle first, so you never create a second account. Sign up in the official app with the alias, phone and vault password, and complete verification yourself. | `done ID handle=<h> created=new` |
| Two-factor | Owner | Turn it on with an authenticator app. Save the backup codes with `vault put .../backup_codes --prompt`. | `done ID method=authenticator_app` |
| Business link | Owner | Switch to a professional business account, then add it as an asset in GAJAN's Meta business | `done ID ig_user_id=<id>` |
| Profile | Owner | Set the name, bio, link, category and image from the brand kit | `done ID done=yes` |
| Roles | Owner | Grant each listed person only the listed permission in Business Suite | `done ID granted=yes` |

The last step reads the profile back through the Meta API and marks the account **active** and **integration-verified**.

## 3. Keep it healthy

- `sweep` every 15 minutes from cron sends reminders and escalates overdue tasks.
- `audit` runs weekly. The `schedule` command prints the cron line.

## 4. Offboarding test

Use the **temporary tester** in the config, whose only role is `insights_viewer`.

1. Grant them that role during provisioning.
2. Run `offboard owner.b@<domain>` (Owner B, leaving).
3. Do the three tasks: remove the role, change the password to the staged one in the vault, and get new backup codes and store them.
4. Run `offboard` again. The report then shows every action completed.

## 5. If Instagram shows a security check or challenge

Stop. Resolve it yourself in the official app. Don't retry, change networks or rotate proxies to get past it. Record it on the task. Automation stays paused until you close the security task.

## 6. Recording (2 to 3 minutes)

Follow [docs/RECORDING_SCRIPT.md](docs/RECORDING_SCRIPT.md).
