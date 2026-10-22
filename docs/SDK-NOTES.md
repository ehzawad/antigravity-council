# Why this release uses native `agy`

The requested model pool is the account-backed Antigravity CLI, including third-party models it advertises. The official Python SDK describes `google-antigravity`, `Agent`/`LocalAgentConfig`, Gemini API-key initialization, Vertex routing, policies, and local model adapters. Those are not evidence that an SDK `Agent` call consumes the same logged-in CLI allocation or exposes third-party models under the same identifiers.

Consequently, this release does **not** claim to be a Python-SDK backend. It follows the documented programmatic CLI route: an authenticated `agy` child, a text user event on stdin, and the native NDJSON result protocol. It uses documented Markdown custom agents as capability profiles. It neither scrapes tokens nor copies browser/keyring credentials into council state.

The CLI and SDK policy APIs are intentionally not mixed. This project does not pass SDK `deny()`/`allow()` objects to a CLI process, invent an `ANTIGRAVITY_CONFIG_DIR` variable, or assume a `models --json` endpoint. A Gemini direct-API provider override or custom Gemini base URL is refused for this account-model workflow; the operator must deliberately change routing themselves.

A future SDK backend should be a separate, tested transport implementation. Its authentication/provider identity, model discovery, cancellation, tool policy, session persistence, and terminal-result contracts need independent verification. Do not substitute it into the current worker path just because the product name is the same.

Sources: [SDK overview](https://antigravity.google/docs/sdk/overview), [SDK policies](https://antigravity.google/docs/sdk/policies/), [installation/authentication tabs](https://antigravity.google/docs/install?tab=cli), and [CLI headless protocol](https://antigravity.google/docs/cli/headless/). Reviewed October 20, 2026.
