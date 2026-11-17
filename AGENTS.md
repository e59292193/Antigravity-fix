# AGENTS.md

If you are an AI agent asked to fix an Antigravity IDE login failure
(`this account is ineligible to use Antigravity`), read:

- **[AGENT.md](AGENT.md)** — the executable runbook (diagnose → patch → verify → rollback)

Quick path:

```bash
python tools/diagnose.py      # prints VERDICT: <code>
python tools/fix_agy.py       # applies the patch if needed
python tools/verify_hub.py    # must print PASS
```

Do not reinstall the IDE and do not switch accounts — both are proven ineffective
for this specific failure. See [docs/root-cause.md](docs/root-cause.md) for evidence.
