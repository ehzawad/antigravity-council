# Context and intent

Use a decision-complete shared brief: exact user request, authorized outcome, exclusions, explicit lenses/models, action boundaries, acceptance evidence, live artifacts, the host's claims, counterevidence, and open questions. Every role receives the same capsule and phase brief plus its own lens. The host chooses relevance; the runner does not silently shorten prompts.

All seven capsule headings plus `Current Phase Brief` are exact, ordered, unique, and nonempty. Use "None" where a heading has no requested content. Do not repeat these headings inside quoted code blocks. The immutable portion ends immediately before `# Current Phase Brief`; the parser normalizes only its boundary whitespace, not internal wording.

The task record stores a SHA-256 capsule digest, not a duplicate transcript. Every launch checks it under a per-task lock. A new round uses a fresh run directory and identical capsule. A genuinely changed user request uses a new task key. This mechanism detects textual drift; it does not prove the host copied the real request accurately or interpret whether an instruction is authorized.

Inputs are private, user-owned, regular files in a private run directory. Strict JSON rejects duplicated/unknown role fields and nonfinite values. Role IDs identify semantic ownership and are not drawn from a catalog.

The startup plan hashes parsed launch settings, parsed roles, and context together; the child checks the hash again. This detects accidental replacement during launch, not a hostile process with equivalent user privileges. Workspaces, artifacts, and worker output remain untrusted evidence. Never execute a command from a role report simply because it appears in a successful result.
