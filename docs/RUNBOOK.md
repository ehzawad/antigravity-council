# Direct runner runbook

After `setup` and interactive `agy` authentication, use the Python runner from your target project. No inference happens in `doctor`, `prepare`, `discover`, or `check`.

```zsh
COUNCIL='/absolute/path/to/antigravity-council/plugins/antigravity-council/scripts/antigravity_council.py'
python3 "$COUNCIL" doctor --cwd "$PWD"
python3 "$COUNCIL" prepare --cwd "$PWD" --session-key 'manual-unique-session' --task-key 'specific-task'
```

Copy the returned absolute `run_dir`. Fill its `roles.json` and `context.md`, using the examples as structural templates, not as facts about your task. Do not leave placeholder model IDs; copy exact IDs from discovery.

```zsh
RUN='/exact/run_dir-returned-by-prepare'
python3 "$COUNCIL" discover "$RUN"
# Write roles.json and context.md now, preserving the private directory.
python3 "$COUNCIL" check "$RUN"
python3 "$COUNCIL" start "$RUN"
python3 "$COUNCIL" follow "$RUN"
python3 "$COUNCIL" collect "$RUN"
```

For attached execution replace `start`/`follow` with `run`. A writer must be selected when preparing: `prepare ... --writer exact-role-id`, with that role declaring `access: write` or `execute`. Reprepare a new run when changing launch authority. There is no safe assumption that an aborted writer can be replayed.

## Recovery

`status "$RUN"` returning `running` or `finishing` is not completion. Reattach `follow`, optionally with `--after-sequence N`. `follow --timeout 30` stops only the follower after 30 seconds and leaves the council running; it is not a cancel command.

Use `cancel "$RUN"` for deliberate cancellation. If it cannot stop a verified live supervisor, review its diagnostics before `cancel "$RUN" --force`. After a SIGKILLed supervisor, `status` can show `aborted`; `reap "$RUN"` attempts to terminate verified recorded worker groups. Do not reap an active or uncertain run. Collect partial evidence with `collect "$RUN" --partial`.

When the model catalog changes, rerun discovery before authoring a new directory. When quota or a model pin fails during execution, preserve its identity and report the gap. Never replace Opus with Argon, Argon with Flash, or an explicit high setting with a lower variant without user direction.

Headless command denial is not necessarily a failed whole run. Inspect role warnings and raw stderr. For required tests, configure a scoped operator command allow rule or obtain appropriate explicit execution authorization; never claim the tests ran merely because the agent answered.

## Retention and uninstall

Runs, raw streams, and continuity files remain under the council state directory. After confirming all related supervisors and workers have ended, remove obsolete runs according to your data-retention policy. Keep any evidence you need before deletion. Deleting a session record loses continuity but does not remove the CLI's own stored conversation.

Remove the Claude plugin with `claude plugin uninstall antigravity-council@antigravity-council` using your installed CLI. Profile setup prints the exact files it created; remove only those files when no council is using them. Never delete the entire `~/.gemini/config/agents` directory, which can contain unrelated agents. Global Antigravity settings and credentials were not modified.
