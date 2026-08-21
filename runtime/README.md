# Private Runtime Overlays

These files are the MindFix source of truth for project-specific changes deployed into the sibling Agora sample worktrees.

The directory layout mirrors `/home/ubuntu/mindfix`:

- `agent-samples/simple-backend/`
- `server-custom-llm/node/`

Check for deployment drift:

```bash
scripts/sync-private-runtime.sh --check
```

Apply the private versions to the sibling worktrees:

```bash
scripts/sync-private-runtime.sh --apply
```

Environment files and runtime data are deliberately excluded. Configure matching custom-LLM inbound secrets in ignored `.env` files as described in `docs/ai/L1/L2/therapy_stack_setup.md`.
