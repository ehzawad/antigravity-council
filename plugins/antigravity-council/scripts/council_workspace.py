"""Where a council works: the user's tree (live), one shared worktree, or one per worker.

A council whose roles can change files works away from the user's tree by default:

- `shared`: one git worktree that every worker edits; for workers building one change.
- `per-role`: one worktree per writing role, all from the same starting snapshot; for
  separate or competing changes that the host compares, then lands the winners.

Every workspace starts from a snapshot commit of exactly what the user sees: tracked
files as they are on disk (including force-added ignored ones), and untracked files
that are not ignored. It is built with a temporary index, so the user's index is never
touched. A git project keeps the snapshot in its own object store; any other folder gets
a private repository in the council state directory.

`diff` commits a workspace's changes on its branch and records the result. `land` applies
exactly that recorded result to the user's tree, all or nothing: it refuses if any path
the council touched changed in the user's tree since the snapshot (content, mode, or type,
or a parent that is no longer a plain directory), backs up what it replaces, writes through
a temporary index, and restores everything on failure. Branches and worktrees are kept.

Git runs with every GIT_* variable removed and hooks disabled. None of this is an OS
isolation boundary: full workers can still reach any path the user's account can.
"""
from __future__ import annotations

import filecmp
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from council_common import CouncilError, ID_RE, now, private_dir, read_json, regular_file, state_root, write_json

MODES = ("auto", "shared", "per-role", "live")
SHARED = "council"  # unit name of the one shared workspace
SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "commit.gpgsign=false",
        "-c", "user.name=antigravity-council", "-c", "user.email=antigravity-council@localhost"]
# Paths that look generated (caches, bytecode, build output). diff flags them so the host
# removes them from the workspace before landing; land itself stays all or nothing.
GENERATED = ("__pycache__/", ".pyc", ".pyo", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", ".tox/",
             "node_modules/", ".DS_Store", ".coverage", "dist/", "build/", ".next/", "target/", ".gradle/")


def _generated(path: str) -> bool:
    return any((g.endswith("/") and ("/" + g in "/" + path)) or (not g.endswith("/") and path.endswith(g))
               for g in GENERATED)


DIFF = ["--no-textconv", "--no-ext-diff", "--no-color", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/", "-U3"]


def effective_mode(setting: str, roles: list[dict]) -> str:
    if setting not in MODES:
        raise CouncilError("workspace must be auto, shared, per-role, or live")
    if setting != "auto":
        return setting
    return "shared" if any(r["access"] != "read" for r in roles) else "live"


def _env(index: Path | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return env


def git(base: list[str], *args: str, stdin: bytes | None = None, index: Path | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(["git", *SAFE, *base, *args], input=stdin, capture_output=True,
                                env=_env(index), timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CouncilError(f"git could not run: {exc}") from None
    if check and result.returncode != 0:
        raise CouncilError(f"git {' '.join(args[:2])} failed: {result.stderr.decode('utf-8', 'replace').strip()[:400]}")
    return result


def _out(result: subprocess.CompletedProcess) -> str:
    return result.stdout.decode().strip()


def _source_repo(root: Path) -> tuple[list[str], bool]:
    """git arguments addressing the user's tree, and whether it is the user's own repository."""
    probe = git(["-C", str(root)], "rev-parse", "--show-toplevel", check=False)
    if probe.returncode == 0 and Path(probe.stdout.decode().strip()).resolve() == root.resolve():
        return ["-C", str(root)], True
    return [], False


def _snapshot(root: Path, run_dir: Path) -> tuple[list[str], list[str], str]:
    """Return (git args for the source tree, git args for the repository, snapshot commit)."""
    src, own = _source_repo(root)
    if own:
        repo = src
        parent = git(src, "rev-parse", "--verify", "-q", "HEAD", check=False)
        parents = ["-p", _out(parent)] if parent.returncode == 0 else []
        user_index = Path(_out(git(src, "rev-parse", "--path-format=absolute", "--git-path", "index")))
    else:
        private = run_dir / "private.git"
        git([], "init", "-q", "--bare", str(private))
        repo = ["--git-dir", str(private)]
        src = repo + ["--work-tree", str(root)]
        parents, user_index = [], None
    with tempfile.TemporaryDirectory(dir=run_dir) as tmp:
        index = Path(tmp) / "index"
        if user_index is not None and user_index.is_file():
            shutil.copyfile(user_index, index)  # keeps force-added files; the user's index is only read
        git(src, "add", "-A", index=index)
        tree = _out(git(src, "write-tree", index=index))
    commit = _out(git(repo, "commit-tree", tree, *parents, "-m", f"antigravity-council: snapshot of {root}"))
    return src, repo, commit


def create(run_dir: Path, root: Path, mode: str, writers: list[str]) -> dict:
    """Create the workspace(s) for one launch and record them in run_dir/workspace.json."""
    if mode == "live":
        record = {"schema": 2, "mode": "live", "source": str(root), "units": {}, "created_at": now()}
        write_json(run_dir / "workspace.json", record)
        return record
    parent = state_root() / "worktrees"
    if parent.resolve().is_relative_to(root.resolve()):
        raise CouncilError("the council state directory is inside the project; a separate workspace is impossible")
    parent = private_dir(parent, create=True)
    names = [SHARED] if mode == "shared" else list(writers)
    if not names or not all(ID_RE.fullmatch(n) for n in names):
        raise CouncilError("per-role workspaces need at least one role that can change files")
    created: list[tuple[Path, str]] = []
    try:
        src, repo, snapshot = _snapshot(root, run_dir)
        units = {}
        for name in names:
            path = parent / run_dir.name / name
            branch = f"antigravity-council-runs/{run_dir.name}/{name}"
            if path.exists() or path.is_symlink():
                raise CouncilError(f"council workspace already exists: {path}")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            git(repo, "worktree", "add", "-q", "-b", branch, str(path), snapshot)
            created.append((path, branch))
            units[name] = {"path": str(path), "branch": branch}
    except BaseException:
        for path, branch in created:  # leave no half-made worktree or branch behind
            git(repo, "worktree", "remove", "--force", str(path), check=False)
            git(repo, "branch", "-D", branch, check=False)
        raise
    record = {"schema": 2, "mode": mode, "source": str(root), "repo": repo, "src": src,
              "own_repo": bool(src and src == repo), "snapshot": snapshot, "units": units, "created_at": now()}
    write_json(run_dir / "workspace.json", record)
    return record


def load(run_dir: Path) -> dict | None:
    path = run_dir / "workspace.json"
    if not path.exists():
        return None
    regular_file(path)
    record = read_json(path)
    if not isinstance(record, dict) or record.get("schema") != 2 or record.get("mode") not in {"live", "shared", "per-role"}:
        raise CouncilError("invalid workspace record")
    return record


def cwd_for(record: dict | None, role: dict) -> str | None:
    """The directory a role works in, or None for the user's tree."""
    if not record or record["mode"] == "live":
        return None
    if record["mode"] == "shared":
        return record["units"][SHARED]["path"]
    unit = record["units"].get(role["id"])
    return unit["path"] if unit else None


def _unit(record: dict | None, name: str | None) -> tuple[str, dict]:
    if record is None or record["mode"] == "live":
        raise CouncilError("this run worked in the user's tree (live); there is nothing to diff or land")
    if record["mode"] == "shared":
        name = name or SHARED
    elif not name:
        raise CouncilError("per-role workspaces need --role ROLE_ID: " + ", ".join(record["units"]))
    if name not in record["units"]:
        raise CouncilError(f"no workspace for {name!r}; have: " + ", ".join(record["units"]))
    unit = record["units"][name]
    if not Path(unit["path"]).is_dir():
        raise CouncilError(f"council workspace is gone: {unit['path']}")
    return name, unit


def _changes(work: list[str], base: str, result: str) -> list[dict]:
    raw = git(work, "diff-tree", "-r", "-z", "--no-renames", "--no-abbrev", base, result).stdout.split(b"\0")
    changes, i = [], 0
    while i + 1 < len(raw) and raw[i]:
        meta, path = raw[i].decode(), raw[i + 1].decode("utf-8", "surrogateescape")
        old_mode, new_mode, _, _, status = meta.lstrip(":").split()
        changes.append({"status": status, "path": path, "old_mode": old_mode, "new_mode": new_mode})
        i += 2
    return changes


def diff(run_dir: Path, name: str | None = None) -> dict:
    """Commit one workspace's changes on its branch, record the result, and write its patch."""
    record = load(run_dir)
    name, unit = _unit(record, name)
    work = ["-C", unit["path"]]
    head = git(work, "symbolic-ref", "-q", "HEAD", check=False)
    if _out(head) != "refs/heads/" + unit["branch"]:
        raise CouncilError(f"workspace {unit['path']} is not on its branch {unit['branch']}; check it out first")
    git(work, "add", "-A")
    # Plumbing, not porcelain diff: a textconv driver can make different files look equal.
    if git(work, "diff-index", "--cached", "--quiet", "HEAD", "--", check=False).returncode:
        git(work, "commit", "-q", "--no-verify", "-m", f"antigravity-council: result of {run_dir.name}/{name}")
    result = _out(git(work, "rev-parse", "HEAD"))
    changes = _changes(work, record["snapshot"], result)
    patch = git(work, "diff-tree", "-r", "-p", "--binary", *DIFF, record["snapshot"], result).stdout
    patch_path = run_dir / f"{name}.patch"
    patch_path.write_bytes(patch)
    os.chmod(patch_path, 0o600)
    reviewed = read_json(run_dir / "diffs.json") if (run_dir / "diffs.json").exists() else {}
    reviewed[name] = {"result": result, "at": now()}
    write_json(run_dir / "diffs.json", reviewed)
    ignored = git(work, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory").stdout
    ignored_paths = [p.decode("utf-8", "surrogateescape") for p in ignored.split(b"\0") if p]
    return {"unit": name, "branch": unit["branch"], "workspace": unit["path"], "source": record["source"],
            "snapshot": record["snapshot"], "result": result, "patch": str(patch_path),
            "files": [{"status": c["status"], "path": c["path"]} for c in changes],
            "not_landable": [c["path"] for c in changes if "160000" in (c["old_mode"], c["new_mode"])],
            # Likely junk from the workers' own runs: remove it in the workspace and diff again.
            "looks_generated": [c["path"] for c in changes if _generated(c["path"])],
            # .gitignore'd files in the workspace are never landed; list them so nothing vanishes unseen.
            "ignored_not_landed": ignored_paths[:200]}


def _current(src: list[str], root: Path, rel: str) -> tuple[str, str] | None:
    """(mode, object id) of a path in the user's tree as git would store it, or None if absent."""
    path = root / rel
    if not (path.exists() or path.is_symlink()):
        return None
    if path.is_symlink():
        oid = _out(git([], "hash-object", "--stdin", "--no-filters", stdin=os.fsencode(os.readlink(path))))
        return "120000", oid
    if not path.is_file():
        return "dir", ""
    mode = "100755" if os.stat(path).st_mode & 0o111 else "100644"
    return mode, _out(git(src, "hash-object", "--path", rel, "--", str(path)))


def _in_snapshot(repo: list[str], commit: str, rel: str) -> tuple[str, str] | None:
    line = git(repo, "ls-tree", "-z", "--full-tree", commit, "--", rel).stdout.split(b"\0")[0]
    if not line:
        return None
    meta = line.split(b"\t", 1)[0].decode().split()
    return meta[0], meta[2]


def _plain_parents(root: Path, rel: str, removed: set[str]) -> bool:
    """No parent of rel is a symlink or a non-directory, unless the council removes that file."""
    current, prefix = root, Path()
    for part in Path(rel).parts[:-1]:
        current, prefix = current / part, prefix / part
        if str(prefix) in removed:
            continue
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            return False
    return True


def _same(a: tuple[str, str] | None, b: tuple[str, str] | None, file_mode: bool) -> bool:
    if a == b or a is None or b is None:
        return a == b
    regular = {"100644", "100755"}
    return not file_mode and a[0] in regular and b[0] in regular and a[1] == b[1]


def _same_entry(a: Path, b: Path) -> bool:
    if a.is_symlink() or b.is_symlink():
        return a.is_symlink() and b.is_symlink() and os.readlink(a) == os.readlink(b)
    if not (a.is_file() and b.is_file()):
        return False
    return (os.stat(a).st_mode & 0o777) == (os.stat(b).st_mode & 0o777) and filecmp.cmp(a, b, shallow=False)


def _restore(root: Path, backup: Path, existed: dict[str, bool]) -> list[str]:
    """Put the user's tree back; best effort per path. Returns paths that could not be restored."""
    failed = []
    for rel, present in existed.items():
        target, saved = root / rel, backup / rel
        try:
            if present:
                if _same_entry(target, saved):
                    continue  # never touched
                if target.exists() or target.is_symlink():
                    target.unlink()
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(saved, target, follow_symlinks=False)
            elif target.exists() or target.is_symlink():
                target.unlink()
        except OSError:
            failed.append(rel)
    return failed


def land(run_dir: Path, name: str | None = None) -> dict:
    """Apply exactly the result `diff` recorded to the user's tree; all or nothing."""
    record = load(run_dir)
    name, unit = _unit(record, name)
    if (run_dir / f"landed-{name}.json").exists():
        raise CouncilError(f"{name} was already landed")
    reviewed = (read_json(run_dir / "diffs.json") if (run_dir / "diffs.json").exists() else {}).get(name)
    if not reviewed:
        raise CouncilError("run diff first and review it; land applies exactly the result diff recorded")
    work = ["-C", unit["path"]]
    if (_out(git(work, "rev-parse", "HEAD")) != reviewed["result"]
            or git(work, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout):
        raise CouncilError("the workspace changed after diff; run diff again and review the new result")
    result, snapshot = reviewed["result"], record["snapshot"]
    if git(work, "merge-base", "--is-ancestor", snapshot, result, check=False).returncode:
        # A branch reset below the snapshot would make land revert the user's own work.
        raise CouncilError("the workspace branch no longer builds on its starting snapshot; land it yourself")
    changes = _changes(work, snapshot, result)
    landable = [c for c in changes if "160000" not in (c["old_mode"], c["new_mode"])]
    if len(landable) != len(changes):
        raise CouncilError("the result changes a submodule; land it yourself: " +
                           ", ".join(c["path"] for c in changes if c not in landable))
    root, src, repo = Path(record["source"]), record["src"], record["repo"]
    if not changes:
        write_json(run_dir / f"landed-{name}.json", {"landed_at": now(), "files": [], "result": result})
        return {"unit": name, "landed": True, "applied": [], "note": "the workspace changed nothing"}
    removed = {c["path"] for c in changes if c["status"] == "D"}
    file_mode = _out(git(src, "config", "--type=bool", "--get", "core.fileMode", check=False)) != "false"
    conflicts = [c["path"] for c in changes
                 if not _plain_parents(root, c["path"], removed)
                 or not _same(_current(src, root, c["path"]), _in_snapshot(repo, snapshot, c["path"]), file_mode)]
    if conflicts:
        raise CouncilError("your tree changed where the council did since it started; nothing was applied: "
                           + ", ".join(conflicts[:20]) + ("" if len(conflicts) <= 20 else ", ...")
                           + f". The council's changes are in {run_dir / (name + '.patch')} and in {unit['path']}; "
                           "take the parts you want from there (merging the branch would also bring its snapshot "
                           "of your earlier work)")
    backup = run_dir / f"land-backup-{name}"
    if backup.exists():
        raise CouncilError(f"a previous land of {name} did not finish cleanly; inspect {backup}")
    backup.mkdir(mode=0o700)
    existed = {c["path"]: (root / c["path"]).exists() or (root / c["path"]).is_symlink() for c in changes}
    for rel, present in existed.items():  # copy, never move: the user's tree is untouched until this succeeds
        if present:
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, backup / rel, follow_symlinks=False)
    try:
        for c in changes:
            if c["status"] == "D":
                (root / c["path"]).unlink()
        for c in changes:  # remove directories the council emptied, before writing in their place
            parent = (root / c["path"]).parent
            while c["status"] == "D" and parent != root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        written = [c["path"] for c in changes if c["status"] != "D"]
        if written:
            with tempfile.TemporaryDirectory(dir=run_dir) as tmp:
                index = Path(tmp) / "index"
                git(repo, "read-tree", result, index=index)
                git(src, "checkout-index", "-f", "-z", "--stdin", index=index,
                    stdin=b"\0".join(os.fsencode(p) for p in written) + b"\0")
    except BaseException as exc:
        unrestored = _restore(root, backup, existed)
        if unrestored:
            raise CouncilError(f"landing failed ({exc}) and these paths could not be restored: "
                               + ", ".join(unrestored) + f"; their originals are in {backup}") from exc
        shutil.rmtree(backup, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    applied = [c["path"] for c in changes]
    write_json(run_dir / f"landed-{name}.json", {"landed_at": now(), "files": applied, "result": result})
    keep = f"branch {unit['branch']} and worktree {unit['path']} are kept"
    if record.get("own_repo"):
        keep += (f"; remove them with `git worktree remove {unit['path']} && git branch -D {unit['branch']}`. "
                 "The branch's snapshot commit holds your uncommitted and untracked files, so do not push it")
    else:
        keep += f"; delete {Path(unit['path']).parent} together with the run directory {run_dir} when done"
    return {"unit": name, "landed": True, "applied": applied, "result": result, "kept": keep}
