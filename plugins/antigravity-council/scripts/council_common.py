"""Private storage, strict inputs, task identity, and process identity. POSIX only."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Any, Iterator

VERSION = "0.2.1"
CONTRACT = 1
TERMINAL = {"succeeded", "partial", "failed", "cancelled"}
ID_RE = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]*\Z")
SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{15,}\Z")
HEADINGS = (
    "Verbatim request", "Authorized outcome", "Hard constraints and non-goals",
    "Explicit lenses or panel requests", "Authority boundaries", "Acceptance evidence",
)


class CouncilError(RuntimeError):
    """An invalid local contract, before inference where possible."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def _pairs(items: list) -> dict:
    out = {}
    for key, value in items:
        if key in out:
            raise CouncilError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def strict_json(text: str) -> Any:
    def bad_number(value: str) -> None:
        raise CouncilError(f"non-finite JSON number: {value}")
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=bad_number)
    except (ValueError, RecursionError) as exc:
        raise CouncilError(f"invalid JSON: {exc}") from exc


def read_json(path: Path) -> Any:
    try:
        return strict_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise CouncilError(f"cannot read {path}: {exc}") from exc


def private_dir(path: Path, *, create: bool = False) -> Path:
    path = Path(os.path.abspath(path.expanduser()))
    # Do not resolve away the final symlink before checking it.
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        st = path.lstat()
    except OSError as exc:
        raise CouncilError(f"private directory unavailable: {path}") from exc
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise CouncilError(f"must be a real user-owned 0700 directory: {path}")
    return path.resolve()


def regular_file(path: Path, *, private: bool = False) -> None:
    try:
        st = path.lstat()
    except OSError as exc:
        raise CouncilError(f"missing file: {path}") from exc
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        raise CouncilError(f"not a regular user-owned file: {path}")
    if private and st.st_mode & 0o077:
        raise CouncilError(f"file must not be accessible to other users: {path}")


def atomic_write(path: Path, data: str | bytes) -> None:
    """Replace one file atomically, with 0600 permissions and durable contents."""
    blob = data.encode("utf-8") if isinstance(data, str) else data
    fd, name = tempfile.mkstemp(prefix=".council-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        with contextlib.suppress(OSError):
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def write_json(path: Path, data: Any) -> None:
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False) + "\n")


def state_root() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return private_dir(base / "antigravity-council", create=True)


def project_root(cwd: str | Path) -> Path:
    root = Path(cwd).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise CouncilError("workspace must be a directory")
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).resolve(strict=True)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return root


def host_session(explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    for key in ("ANTIGRAVITY_COUNCIL_SESSION_KEY", "CLAUDE_CODE_SESSION_ID",
                "CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_SESSION_ID"):
        if os.environ.get(key, "").strip():
            return key + "=" + os.environ[key].strip()
    raise CouncilError("no host session ID; pass --session-key explicitly (no project-wide fallback)")


def positive(value: Any, label: str, *, zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CouncilError(f"{label} must be finite numeric")
    if value < 0 or (value == 0 and not zero):
        raise CouncilError(f"{label} must be {'nonnegative' if zero else 'positive'}")
    return float(value)


def intent_capsule(context: str) -> str:
    """Validate exact section boundaries, rather than searching for substrings."""
    if context.count("# Intent Capsule\n") != 1 or context.count("# Current Phase Brief\n") != 1:
        raise CouncilError("context needs one # Intent Capsule and one # Current Phase Brief")
    prefix, phase = context.split("# Current Phase Brief\n", 1)
    if not prefix.startswith("# Intent Capsule\n") or not phase.strip():
        raise CouncilError("capsule must be first and the phase brief must be nonempty")
    matches = list(re.finditer(r"^## (.+)$", prefix, re.M))
    if [m.group(1) for m in matches] != list(HEADINGS):
        raise CouncilError("capsule headings must be complete, unique, and in template order")
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prefix)
        if not prefix[match.end():end].strip():
            raise CouncilError(f"empty capsule section: {match.group(1)}")
    return prefix.rstrip() + "\n"


def parse_roles(raw: Any) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise CouncilError("roles.json must be a nonempty array; there is no fixed panel")
    roles, seen = [], set()
    allowed = {"id", "label", "instruction", "model", "access", "selection_reason"}
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) - allowed:
            raise CouncilError("role has invalid or unknown fields")
        if not all(isinstance(entry.get(k), str) and entry[k].strip()
                   for k in ("id", "label", "selection_reason")):
            raise CouncilError("each role needs id, label, and selection_reason")
        rid = entry["id"]
        if not ID_RE.fullmatch(rid) or rid in seen:
            raise CouncilError(f"invalid or duplicate role ID: {rid!r}")
        # Omitting model inherits agy's own configured default; there is no
        # literal inherit/default placeholder and no built-in model list.
        if "model" in entry:
            model = entry["model"]
            if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
                raise CouncilError("model must be an exact safe execution slug, or omitted to inherit agy's default")
            if model.lower() in {"inherit", "default"}:
                raise CouncilError("inherit agy's default model by omitting model, not with a placeholder")
        if any(c in entry["label"] + entry["selection_reason"] for c in "\r\n\x00"):
            raise CouncilError("label and selection_reason must be single-line")
        instruction = entry.get("instruction")
        if not isinstance(instruction, list) or not instruction or not all(
            isinstance(s, str) and s.strip() for s in instruction
        ):
            raise CouncilError("instruction must be a nonempty list of nonempty strings")
        access = entry.get("access", "read")
        if not isinstance(access, str) or access not in {"read", "write", "execute"}:
            raise CouncilError("access must be read, write, or execute")
        role = dict(entry, access=access)
        # Validate UTF-8 before any subprocess is created.
        json.dumps(role, ensure_ascii=False).encode("utf-8")
        roles.append(role)
        seen.add(rid)
    return roles


def open_lock(path: Path) -> int:
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        os.close(fd)
        raise CouncilError(f"unsafe lock file: {path}")
    return fd


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    fd = open_lock(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def lock_held(path: Path) -> bool | None:
    """Read without creating. None means unknown, never permission to signal."""
    try:
        regular_file(path, private=True)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except (OSError, CouncilError):
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)


def process_identity(pid: int) -> dict | None:
    """Birth identity and process group, rejecting zombies and reused PIDs."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        return None
    if Path("/proc").is_dir():
        try:
            text = Path(f"/proc/{pid}/stat").read_text()
            rest = text[text.rfind(")") + 2:].split()
            if rest[0] == "Z":
                return None
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return {"pid": pid, "birth": boot + ":" + rest[19], "pgid": int(rest[2])}
        except (OSError, IndexError, ValueError):
            return None
    try:
        p = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "pgid=", "-o", "stat="],
                           capture_output=True, text=True, timeout=3, check=False)
        parts = p.stdout.split()
        if p.returncode or len(parts) < 7 or parts[-1].startswith("Z"):
            return None
        return {"pid": pid, "birth": " ".join(parts[:5]), "pgid": int(parts[-2])}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def same_process(record: dict) -> bool:
    current = process_identity(record.get("pid"))
    return current is not None and current == {k: record.get(k) for k in ("pid", "birth", "pgid")}
