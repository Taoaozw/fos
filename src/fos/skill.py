"""Embedded AI Skill installer and auto-refresher (learned from fops)."""

from __future__ import annotations

import os
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

from fos.errors import UsageError

# User-level skill directories of common AI coding tools.
# A root is auto-selected when its tool directory (parent) exists.
SKILL_ROOTS: tuple[str, ...] = (
    ".gemini/config/skills",
    ".claude/skills",
    ".codex/skills",
    ".agents/skills",
    ".cursor/skills",
    ".pi/agent/skills",
)


def _bundle_files() -> list[tuple[str, bytes]]:
    """Read all files embedded inside `fos.skill_bundle`."""
    root = resources.files("fos.skill_bundle")
    out: list[tuple[str, bytes]] = []

    def _walk(node: Any, prefix: str) -> None:
        for child in node.iterdir():
            name = child.name
            if name.startswith("__") or name.endswith(".pyc"):
                continue
            rel = f"{prefix}/{name}" if prefix else name
            if child.is_dir():
                _walk(child, rel)
            else:
                out.append((rel, child.read_bytes()))

    _walk(root, "")
    return out


def list_installed_skills(*, home: Path | None = None) -> list[str]:
    user_home = (home or Path.home()).resolve()
    found: list[str] = []
    for rel_root in SKILL_ROOTS:
        dest = user_home / rel_root / "fos"
        if (dest / "SKILL.md").exists():
            found.append(str(dest))
    return found


def install_skill(
    *,
    target_dir: Path | None = None,
    refresh_only: bool = False,
    home: Path | None = None,
) -> dict[str, Any]:
    """Install embedded fos skill bundle (SKILL.md + references/) into AI tool skill roots."""
    user_home = (home or Path.home()).resolve()
    targets: list[Path] = []

    if target_dir is not None:
        targets = [Path(target_dir).expanduser().resolve()]
    else:
        for rel_root in SKILL_ROOTS:
            root = user_home / rel_root
            dest = root / "fos"
            if refresh_only:
                if dest.exists() or dest.is_symlink():
                    targets.append(root)
            elif root.parent.exists():
                targets.append(root)

        if not targets:
            if refresh_only:
                return {"installed": [], "refreshed_only": True}
            raise UsageError(
                "No AI tool directory found (~/.gemini/config, ~/.claude, ~/.codex, ~/.agents, ~/.cursor).",
                hint="pass --dir <skills_dir> to specify target directory",
            )

    files = _bundle_files()
    installed: list[str] = []

    for root in targets:
        dest_base = root / "fos"
        # If previously symlinked to a repo checkout, replace symlink with managed copy so self-updates work anywhere
        if dest_base.is_symlink():
            dest_base.unlink()
        elif dest_base.exists() and not dest_base.is_dir():
            dest_base.unlink()
        dest_base.mkdir(parents=True, exist_ok=True)

        for rel_path, content in files:
            dest_file = dest_base / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest_file.with_suffix(dest_file.suffix + ".tmp")
            tmp.write_bytes(content)
            os.chmod(tmp, 0o644)
            os.replace(tmp, dest_file)

        installed.append(str(dest_base))

    return {
        "installed": installed,
        "files": [rel for rel, _ in files],
        "refreshed_only": refresh_only,
    }


def refresh_installed_skills(*, home: Path | None = None) -> dict[str, Any]:
    """Silently refresh only already-installed fos skill directories after CLI self-update."""
    return install_skill(refresh_only=True, home=home)


def remove_skill_symlinks_if_needed(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
