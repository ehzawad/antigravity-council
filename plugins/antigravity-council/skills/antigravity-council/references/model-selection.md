# Model selection contract

There is no built-in model list, preference table, or ranking. The Antigravity catalog changes often (new variants roughly monthly, old ones retired), so every choice comes from the run's live discovery.

Discover via documented `agy models`, not an assumed JSON API. The output parser accepts a slug/label table and refuses an empty or conflicting catalog. `doctor` also checks CLI help and version. These probes make no agent turn; they do not validate remaining credits or prove a listed model is authorized for inference. Catalog labels are data, not instructions.

Each role chooses one of two shapes:

- **Pinned**: `"model": "<exact ID from this run's discovery>"`. An explicit user choice is resolved to its exact catalog ID and wins. Preflight refuses an ID the catalog does not list; report that gap instead of substituting.
- **Inherited**: omit `model`. No `--model` is sent and agy serves the role with its own configured default. Literal `inherit` or `default` values are refused; inheritance is omission.

Every role records a `selection_reason`. Do not infer capability from product names, ID spelling, version numbers, catalog order, or remembered reputations; use the user's words and the advertised labels.

No separate effort override is sent. agy encodes reasoning effort in variant IDs, so the exact variant is the effort choice, avoiding contradictory `--model`/`--effort` combinations. Unknown role fields, including `effort`, are refused.

Preflight checks the staged catalog. `start` refreshes it before dispatch. Failure after dispatch is a role failure; quota and model rejection never trigger a substitute. Auth errors require operator login. Only a read-only transient error with no observed tool work can retry once on the same model; writers never retry automatically.

For a pinned role, the runtime checks `init.model` against the pin; agy reports `init.model` only when `--model` overrides it, so an inherited role has no requested model to compare and its report shows the pin as absent. `init.agent` is always checked against the capability profile. This is CLI telemetry, not cryptographic provider attestation. Result usage is labeled as CLI conversation/session cumulative and is not summed into a fictitious marginal bill.

Session keys include the exact model (or its absence) and capability profile. Cross-model checks therefore begin independently. Selective host-provided evidence may be staged in a subsequent phase, explicitly labeled as claims to verify rather than established truth.
