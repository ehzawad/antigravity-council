# Security model

This plugin coordinates trusted, authenticated local agent CLIs. It is not a secure multi-tenant service, an untrusted-code container, or a proof that model-generated actions satisfy a user's intent.

## Implemented controls

Inputs and state are private, user-owned files. Strict schemas, immutable intent hashes, input hashes, exact model pins, custom-agent tool inventories, a single mutator, workspace locks, and process-identity checks prevent specific accidents and fail closed on missing evidence. Profile setup refuses conflicting content. The runtime does not change global settings or deliberately export credentials.

Reader workers are given only the documented file-reading/search tools. Writers add file tools; executors additionally get shell. Runtime init/tool events are checked. A reviewer has no shell just because a command sounds read-only: redirections, test fixtures, language startup hooks, and build scripts can mutate files.

Permission bypass is off by default. A specifically authorized executor may use it. Explicit `--sandbox` requests are vendor behavior, not containment this Python process independently provides. File-edit permission does not authorize commits, deployment, publishing, or remote operations.

## Limits that remain

Processes run as the same OS user. An executor can potentially reach files that a reader cannot, including plugin code, credentials accessible to that user, or reports. A hash and 0700 directory do not protect against that same user's malicious process. Only an OS-level boundary can offer stronger containment.

Custom-agent configuration is a vendor contract. CLI changes, global hooks/MCPs/plugins, profile shadowing, and startup behavior can weaken isolation. Known workspace profile shadow paths are refused and effective tools are audited, but this is not exhaustive hostile-configuration analysis. An observed tool event can arrive after work has begun. The original workspace and machine configuration must be trusted. Observed on `agy` 1.4.4: a read-only worker reported a system message asking it to configure a user-installed agy plugin (one that ships hooks), despite the profile's `plugins: []`. The worker declined, but enabled agy plugins and hooks can still reach workers.

Read/search tools can expose sensitive material to the selected provider. The plugin does not redact code or apply a data-loss-prevention policy. Raw streams, prompts, and error messages may include secrets. Stored files use restrictive modes, but operators remain responsible for retention, backups, and who can access their account.

Retries are deliberately narrow and never apply to mutators. Even read-only retry safety depends on CLI reports. No exactly-once execution is promised. Successful transport output is not proof of correctness, clean tests, or successful command execution; headless permission denials can be soft failures.

Process groups enable best-effort cancellation. Tools that create new sessions, reparent, or run externally may outlive a killed worker; a report of supervisor exit is not a universal proof that every descendant in the system stopped. `reap` only signals verified recorded worker identities after a verified supervisor death. Reboots, PID namespaces, filesystem issues, and suspended hosts remain external failure modes.

Read fleets and writer fleets coordinate only with this plugin's project lock. Manual editors and other tools do not participate. Readers inside a mixed fleet can inspect intermediate writer state. Independent verification must be repeated on settled artifacts.

## Recommended operation

Begin with `read`, scoped operator command policies, and a trusted repository. Run the opt-in live smoke test on the installed CLI version before depending on capability behavior. Use an isolated VM/container or an appropriately restricted OS account for untrusted projects. Keep authenticated local agent access away from public web services. Never claim the offline fake-CLI suite certifies the current vendor binary.

Report problems privately to the repository owner before publishing sensitive logs. The package has no telemetry endpoint and does not send reports anywhere beyond the CLI requests the operator launches.
