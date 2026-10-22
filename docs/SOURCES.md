# Source contract ledger

Reviewed against official public documentation on **October 22, 2026**, with the installed `agy` 1.4.4 `--help` and `agy changelog`. Differences between the two are listed in [RECONCILIATION.md](RECONCILIATION.md). URLs are live and may change after that date. The implementation refuses missing capabilities rather than claiming compatibility with every historic CLI version.

| Source | Contract used |
|---|---|
| [CLI headless mode](https://antigravity.google/docs/cli/headless/) | Streaming stdin user messages, `event` envelopes, explicit conversation resume, model/agent pins, terminal states, cumulative counters, native response timeout |
| [CLI execution modes](https://antigravity.google/docs/cli/modes/) | `--mode accept-edits`; plan mode is not treated as a security boundary |
| [Available models](https://antigravity.google/docs/models/) | Availability depends on product/plan and changes often; local metadata and inference are distinct checks |
| [Enterprise model configuration](https://antigravity.google/docs/enterprise/) | Variant identifiers encode effort choices; background coordination models can differ from the requested reasoning model |
| [Reasoning effort](https://antigravity.google/docs/cli/commands/effort/) | Effort is model-dependent; this adapter pins discovered variant IDs without a second overriding effort field |
| [Custom agents](https://antigravity.google/docs/subagents/) | Global Markdown profiles, explicit tool lists, main-agent selection, shell policy, and optional extensions |
| [Terminal sandbox](https://antigravity.google/docs/sandbox/) | `--sandbox` requests session-level terminal isolation; it is not a Python-enforced security boundary |
| [Permissions](https://antigravity.google/docs/permissions/) | CLI operator policies and the distinction between scoped grants and blanket bypass |
| [Install and authenticate](https://antigravity.google/docs/install?tab=cli) | Native cached CLI credentials, provider overrides, and the separately documented SDK authentication path |
| [SDK overview](https://antigravity.google/docs/sdk/overview) | Reviewed to avoid conflating the Python SDK API with account-backed CLI execution |
| [SDK policies](https://antigravity.google/docs/sdk/policies/) | SDK policy functions are not passed to or advertised as CLI flags |
| [SDK lifecycle](https://antigravity.google/docs/sdk/lifecycle/) | Separate SDK session contract; not transplanted into the CLI adapter |
| [Claude plugin manifest](https://code.claude.com/docs/en/plugins/manifest-reference) | Plugin manifest, conventional component directories, plugin-relative scripts |
| [Claude marketplace creation](https://code.claude.com/docs/en/plugins/create-marketplace) | GitHub marketplace registration, plugin installation, version-pinned updates |
| [Claude skills](https://code.claude.com/docs/en/skills) | Turn-scoped `allowed-tools` pre-approval of the runner command |
| [Claude dynamic workflows](https://code.claude.com/docs/en/workflows) | Plugin `workflows/` distribution, `meta` literal, `agent`/`pipeline`/`phase`, `args` |

Architecture references: [Codex Council](https://github.com/ehzawad/codex-council) and [Cursor Cult](https://github.com/ehzawad/cursor-cult). Their source was inspected through the connected GitHub account. The new plugin uses its own runtime modules and Antigravity protocol implementation; it does not assume the original projects' vendor flags transfer.

No undocumented SDK account transport, metadata JSON flag, unlimited native timeout value, or force-permission default is asserted. Code, tests, and docs record intentional assumptions and local verification gaps.
