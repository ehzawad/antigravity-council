# Model selection contract

Discover via documented `agy models`, not an assumed JSON API. The output parser accepts a slug/name table and refuses an empty or conflicting catalog. `doctor` also checks CLI help and version. These probes make no agent turn; they do not validate remaining credits or prove a listed model is authorized for inference.

Preferred Argon variants: `gemini-4-argon-high` and `gemini-4-argon-medium`. Preferred Opus 5.5 variants: `claude-opus-5-5-high`, `claude-opus-5-5-medium`, `claude-opus-5-5-max`, and `claude-opus-5-5-low`. These are preference hints, not a hardcoded availability assertion or performance ranking. Use only exact IDs returned by this account's current CLI.

Every role records an exact `model` and `selection_reason`. An explicit user pin wins. If unavailable, explain the failure without substituting another model. New preferred variants can be added only after documentation and local metadata support them. Other advertised user-selected models are accepted.

No separate effort override is sent. Reasoning variants are selected through the exact ID, avoiding contradictory `--model`/`--effort` combinations. Unknown role fields, including `effort`, are refused. Do not silently infer a default model.

Preflight checks the staged catalog. `start` refreshes it before dispatch. Failure after dispatch is a role failure; quota and model rejection never trigger a substitute. Auth errors require operator login. Only a read-only transient error with no observed tool work can retry once on the same model; writers never retry automatically.

The runtime validates `init.model` and `init.agent` against the pin and capability profile. This is CLI telemetry, not cryptographic provider attestation. Result usage is labeled as CLI conversation/session cumulative and is not summed into a fictitious marginal bill.

Session keys include the exact model and capability profile. Cross-model checks therefore begin independently. Selective host-provided evidence may be staged in a subsequent phase, explicitly labeled as claims to verify rather than established truth.
