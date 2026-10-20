"""Only discover through documented CLI metadata commands. Never infer entitlement."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

from council_common import CouncilError, MODEL_RE, digest, now, read_json, write_json

REQUIRED_FLAGS = {"--input-format", "--output-format", "--model", "--agent", "--conversation", "--print-timeout"}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# Preferences, not a fabricated catalog: membership in agy models is mandatory.
PREFERENCES = {
    "argon": ("gemini-4-argon-high", "gemini-4-argon-medium"),
    "opus-5.5": ("claude-opus-5-5-high", "claude-opus-5-5-medium", "claude-opus-5-5-max", "claude-opus-5-5-low"),
}


def cli_binary() -> str:
    requested = os.environ.get("ANTIGRAVITY_COUNCIL_AGY", "agy")
    found = shutil.which(requested)
    if not found:
        raise CouncilError("agy not found; install official Antigravity CLI and authenticate interactively")
    return str(Path(found).resolve())


def probe(argv: list[str], cwd: Path, timeout: float = 20, include_stderr: bool = False) -> str:
    """Bound only metadata probes, never silently spend on a test prompt.

    The real agy prints `--help` usage on stderr (Go flag convention, exit 0),
    so help probes opt into reading both streams.
    """
    try:
        result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CouncilError(f"metadata probe failed: {argv[1:]}: {exc}") from exc
    if result.returncode:
        raise CouncilError(f"metadata probe exited {result.returncode}: {result.stderr[-2000:]}")
    text = result.stdout + (result.stderr if include_stderr else "")
    return ANSI.sub("", text)


def parse_models(text: str) -> list[dict]:
    """Parse the documented slug + display-name table, not imaginary JSON fields.

    Unknown line formats are ignored. No recognized rows is a hard error; operators
    see the raw metadata file and can report a CLI output format change.
    """
    models = {}
    for line in ANSI.sub("", text).splitlines():
        line = line.strip().strip("│|").strip()
        if not line or line.startswith(("#", "Available", "Model", "NAME", "SLUG")):
            continue
        parts = re.split(r"\s{2,}|\s*[│|]\s*", line, maxsplit=1)
        if len(parts) != 2:
            parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        slug, label = (p.strip() for p in parts)
        # A hyphenated slug avoids misreading prose and table headings.
        if not MODEL_RE.fullmatch(slug) or "-" not in slug or not label:
            continue
        if slug in models and models[slug] != label:
            raise CouncilError(f"conflicting catalog rows for {slug}")
        models[slug] = label
    if not models:
        raise CouncilError("agy models returned no recognizable slug rows; no guessed fallback")
    return [{"id": slug, "label": label} for slug, label in models.items()]


def provider_check() -> dict:
    """Observe documented provider overrides without changing auth or settings."""
    path = Path.home() / ".gemini/antigravity-cli/settings.json"
    settings = read_json(path) if path.exists() else {}
    if not isinstance(settings, dict):
        raise CouncilError("Antigravity settings must be a JSON object")
    if settings.get("modelProvider") == "gemini":
        raise CouncilError("modelProvider=gemini selects Gemini API, not Antigravity account models; "
                           "restore account routing yourself before using this plugin")
    if os.environ.get("GOOGLE_GEMINI_BASE_URL"):
        raise CouncilError("GOOGLE_GEMINI_BASE_URL is set; custom endpoint routing is not verified")
    return {"route": "native-account-or-adc", "adc_env": bool(os.environ.get("AGY_ADC_AUTH")),
            "entitlement_verified": False}


def discover(root: Path) -> dict:
    route = provider_check()
    binary = cli_binary()
    version = probe([binary, "--version"], root).strip()
    help_text = probe([binary, "--help"], root, include_stderr=True)
    flags = set(re.findall(r"--[a-z][a-z0-9-]+", help_text))
    missing = sorted(REQUIRED_FLAGS - flags)
    if missing:
        raise CouncilError("installed agy help does not advertise required flags: " + ", ".join(missing))
    text = probe([binary, "models"], root)
    models = parse_models(text)
    ids = {m["id"] for m in models}
    return {"schema": 1, "created_at": now(), "binary": binary, "version": version,
            "flags": sorted(flags), "models": models, "raw_models": text, "auth": route,
            "preferred": {k: [s for s in v if s in ids] for k, v in PREFERENCES.items()},
            "snapshot_id": digest(text + version + str(root))[:20]}


def validate_models(roles: list[dict], snapshot: dict) -> None:
    ids = {m["id"] for m in snapshot["models"]}
    for role in roles:
        if role["model"] not in ids:
            raise CouncilError(f"{role['id']}: requested model {role['model']!r} not advertised by agy models; "
                               "refresh discovery, preserve the user's pin, and report the gap")


def store_discovery(run_dir: Path, root: Path) -> dict:
    snapshot = discover(root)
    write_json(run_dir / "model-snapshot.json", snapshot)
    return snapshot
