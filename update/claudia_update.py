#!/usr/bin/env python3
# Made with Grok Bot.
"""Compare local Claudia tools to the hosted VERSION and self-update safely.

Steps:
  1. Read remote VERSION (or --version-url).
  2. If newer than local VERSION, download every path listed in MANIFEST.sha256
     into a temporary staging directory.
  3. Verify sha256 of every staged file against the manifest.
  4. Atomically swap staged files into place. On any failure, leave old files
     untouched and exit non-zero.

Also provides:
  python3 claudia_update.py check     # report only
  python3 claudia_update.py update    # download + verify + swap when newer
  python3 claudia_update.py manifest  # regenerate MANIFEST.sha256 from repo root
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://raw.githubusercontent.com/kbanc85/claudia-grokbot/main"
DEFAULT_VERSION_URL = f"{DEFAULT_BASE}/VERSION"
DEFAULT_MANIFEST_URL = f"{DEFAULT_BASE}/MANIFEST.sha256"

# Paths included in the manifest when regenerating from a checkout.
MANIFEST_GLOBS = [
    "VERSION",
    "MANIFEST.sha256",
    "docs/FOR-BOTS.md",
    "ledger/claudia_ledger.py",
    "ledger/templates/*.csv",
    "brain/claudia-brain.canvas.tsx",
    "brain/README.md",
    "templates/*.txt",
    "templates/claudia_render.py",
    "templates/README.md",
    "scan/claudia_scan.py",
    "scan/README.md",
    "update/claudia_update.py",
    "setup/first_run.sh",
    "setup/README.md",
]


class UpdateError(Exception):
    pass


def parse_version(text: str) -> tuple[int, ...]:
    """Parse the first line of VERSION as dotted integers (1.1.0 -> (1,1,0))."""
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if not line:
        raise UpdateError("empty VERSION")
    parts: list[int] = []
    for bit in line.split("."):
        if not bit.isdigit():
            raise UpdateError(f"bad VERSION token {bit!r} in {line!r}")
        parts.append(int(bit))
    return tuple(parts)


def version_gt(remote: tuple[int, ...], local: tuple[int, ...]) -> bool:
    # Pad shorter tuples with zeros.
    n = max(len(remote), len(local))
    r = remote + (0,) * (n - len(remote))
    l = local + (0,) * (n - len(local))
    return r > l


def fetch_bytes(url: str, timeout: float = 30.0) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.URLError as e:
        raise UpdateError(f"download failed for {url}: {e}") from e


def fetch_text(url: str) -> str:
    return fetch_bytes(url).decode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_manifest(text: str) -> dict[str, str]:
    """Parse `hash  path` lines (two spaces, sha256sum format)."""
    out: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            raise UpdateError(f"manifest line {lineno}: expected 'hash  path'")
        digest, path = parts
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise UpdateError(f"manifest line {lineno}: bad sha256")
        out[path] = digest
    if not out:
        raise UpdateError("manifest is empty")
    return out


def expand_manifest_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in MANIFEST_GLOBS:
        if "*" in pattern:
            paths.extend(sorted(root.glob(pattern)))
        else:
            p = root / pattern
            if p.is_file():
                paths.append(p)
    # Unique, stable, relative paths only (files).
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel in seen:
            continue
        # Do not hash MANIFEST itself into itself during generation until the
        # end; we still list VERSION and other tools.
        if rel == "MANIFEST.sha256":
            continue
        seen.add(rel)
        out.append(p)
    return out


def cmd_manifest(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    paths = expand_manifest_paths(root)
    if not paths:
        raise UpdateError(f"no files matched under {root}")
    lines = ["# Made with Grok Bot.", "# sha256  relative/path"]
    rows: list[tuple[str, str]] = []
    for p in paths:
        rel = p.relative_to(root).as_posix()
        rows.append((sha256_file(p), rel))
    rows.sort(key=lambda t: t[1])
    for digest, rel in rows:
        lines.append(f"{digest}  {rel}")
    text = "\n".join(lines) + "\n"
    out = root / "MANIFEST.sha256"
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(rows)} files)")
    return 0


def read_local_version(tools_dir: Path) -> tuple[int, ...] | None:
    path = tools_dir / "VERSION"
    if not path.is_file():
        return None
    return parse_version(path.read_text(encoding="utf-8"))


def cmd_check(args: argparse.Namespace) -> int:
    tools = Path(args.tools_dir)
    local = read_local_version(tools)
    remote_text = fetch_text(args.version_url)
    remote = parse_version(remote_text)
    local_s = ".".join(str(x) for x in local) if local else "(none)"
    remote_s = ".".join(str(x) for x in remote)
    print(f"local:  {local_s}")
    print(f"remote: {remote_s}")
    if local is None:
        print("status: missing local VERSION (update recommended)")
        return 2
    if version_gt(remote, local):
        print("status: update available")
        return 2
    print("status: up to date")
    return 0


def atomic_replace(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write via tempfile in the destination directory, then os.replace.
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def cmd_update(args: argparse.Namespace) -> int:
    tools = Path(args.tools_dir)
    tools.mkdir(parents=True, exist_ok=True)
    local = read_local_version(tools)
    remote_text = fetch_text(args.version_url)
    remote = parse_version(remote_text)
    if local is not None and not version_gt(remote, local) and not args.force:
        print(
            f"up to date (local {'.'.join(map(str, local))}, "
            f"remote {'.'.join(map(str, remote))})"
        )
        return 0

    manifest_text = fetch_text(args.manifest_url)
    manifest = parse_manifest(manifest_text)
    # Stage everything under a temp dir; only swap after full verify.
    staging = Path(tempfile.mkdtemp(prefix="claudia-update-"))
    try:
        staged: dict[str, Path] = {}
        base = args.base_url.rstrip("/")
        for rel, expected in manifest.items():
            url = f"{base}/{rel}"
            data = fetch_bytes(url)
            got = sha256_bytes(data)
            if got != expected:
                raise UpdateError(
                    f"checksum mismatch for {rel}: expected {expected}, got {got}. "
                    "Old files left untouched."
                )
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            staged[rel] = dest

        # Also stage the manifest and VERSION we just fetched for local record.
        (staging / "MANIFEST.sha256").write_text(manifest_text, encoding="utf-8")
        (staging / "VERSION").write_text(remote_text, encoding="utf-8")

        # Atomic swap each file into tools_dir mirroring repo-relative paths.
        for rel, src in sorted(staged.items()):
            atomic_replace(src, tools / rel)
        atomic_replace(staging / "MANIFEST.sha256", tools / "MANIFEST.sha256")
        atomic_replace(staging / "VERSION", tools / "VERSION")
        print(
            f"updated to {'.'.join(map(str, remote))} "
            f"({len(staged)} files verified and swapped)"
        )
        return 0
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f"update failed; old files left untouched: {e}") from e
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claudia_update.py",
        description=(
            "Version check and atomic self-update for Claudia tools. "
            "Made with Grok Bot."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Compare local VERSION to remote")
    p_check.add_argument(
        "--tools-dir",
        default="/workspace/claudia-tools",
        help="Local tools folder that holds VERSION",
    )
    p_check.add_argument("--version-url", default=DEFAULT_VERSION_URL)
    p_check.set_defaults(func=cmd_check)

    p_upd = sub.add_parser("update", help="Download, verify, and swap when newer")
    p_upd.add_argument("--tools-dir", default="/workspace/claudia-tools")
    p_upd.add_argument("--version-url", default=DEFAULT_VERSION_URL)
    p_upd.add_argument("--manifest-url", default=DEFAULT_MANIFEST_URL)
    p_upd.add_argument("--base-url", default=DEFAULT_BASE)
    p_upd.add_argument(
        "--force",
        action="store_true",
        help="Re-download even when versions match",
    )
    p_upd.set_defaults(func=cmd_update)

    p_man = sub.add_parser(
        "manifest",
        help="Regenerate MANIFEST.sha256 from the repo checkout",
    )
    p_man.add_argument(
        "--root",
        default=".",
        help="Repo root (default: current directory)",
    )
    p_man.set_defaults(func=cmd_manifest)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except UpdateError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
