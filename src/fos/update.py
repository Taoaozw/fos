"""Self-update, detached background auto-update, SHA-256 wheel verification, and brain dist publishing (learned from fops)."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fos import __version__
from fos.config import FosConfig
from fos.errors import AuthError, FosError, OfflineError, UsageError

AUTO_UPDATE_SPAWN_DEBOUNCE_S = 120
AUTO_UPDATE_FAIL_COOLDOWN_S = 1800
UPDATE_LOCK_STALE_S = 300

_CALVER_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}-\d{4}$")
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.](.+))?$")


@dataclass
class UpdateState:
    last_spawn_at: int = 0
    last_attempt_at: int = 0
    failed_version: str = ""
    last_error: str = ""
    updated_from: str = ""
    updated_to: str = ""
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UpdateState:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def read_update_state(path: Path) -> UpdateState:
    if not path.exists():
        return UpdateState()
    try:
        return UpdateState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return UpdateState()


def save_update_state(path: Path, state: UpdateState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def auto_update_enabled() -> bool:
    return os.environ.get("FOS_NO_AUTOUPDATE") != "1"


def is_editable_install() -> bool:
    """Detect whether fos is installed as an editable dev checkout (never auto-overwritten in background)."""
    try:
        dist = importlib.metadata.distribution("fos")
        direct = dist.read_text("direct_url.json")
        if direct:
            info = json.loads(direct)
            return bool((info.get("dir_info") or {}).get("editable"))
    except Exception:
        pass
    return False


def _parse_semver(v: str) -> tuple[int, int, int, str] | None:
    m = _SEMVER_RE.match(v.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4) or "")


def should_auto_update(local: str, remote: str) -> bool:
    """Decide whether local version should auto-upgrade to remote (only moves forward; skips dev/dirty)."""
    loc = local.strip()
    rem = remote.strip()
    if not loc or not rem or loc == rem:
        return False
    if loc == "dev" or "+dirty" in loc:
        return False
    if _CALVER_RE.match(loc) and _CALVER_RE.match(rem):
        return rem > loc
    s_loc = _parse_semver(loc)
    s_rem = _parse_semver(rem)
    if s_loc is not None and s_rem is not None:
        return s_rem[:3] > s_loc[:3] or (s_rem[:3] == s_loc[:3] and s_loc[3] != "" and s_rem[3] == "")
    return True


def _acquire_update_lock(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age < UPDATE_LOCK_STALE_S:
                    return False
                lock_path.unlink(missing_ok=True)
            except OSError:
                return False
        except OSError:
            return False
    return False


def _release_update_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def _resolve_fos_executable() -> list[str]:
    fos_bin = shutil.which("fos")
    if fos_bin:
        return [fos_bin]
    return [sys.executable, "-m", "fos.cli"]


def maybe_trigger_auto_update(
    cache_dir: Path,
    remote_version: str,
    *,
    home: Path | None = None,
    local_version: str = __version__,
) -> bool:
    """Spawn a detached `fos update --background` process when brain advertises a newer CLI build.

    Never blocks or prints to stdout. Returns True if a background spawn was initiated.
    """
    if not auto_update_enabled() or is_editable_install() or not should_auto_update(local_version, remote_version):
        return False

    now = int(time.time())
    state_path = cache_dir / "update_state.json"
    st = read_update_state(state_path)
    if st.failed_version == remote_version and (now - st.last_attempt_at) < AUTO_UPDATE_FAIL_COOLDOWN_S:
        return False
    if (now - st.last_spawn_at) < AUTO_UPDATE_SPAWN_DEBOUNCE_S:
        return False

    st.last_spawn_at = now
    save_update_state(state_path, st)

    cmd = _resolve_fos_executable()
    if home is not None:
        cmd += ["--home", str(home)]
    cmd += ["update", "--background"]

    env = dict(os.environ)
    env["FOS_NO_AUTOUPDATE"] = "1"
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
        return True
    except OSError:
        return False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _install_wheel_file(wheel_path: Path) -> str:
    uv_bin = shutil.which("uv")
    if uv_bin:
        proc = subprocess.run(
            [uv_bin, "tool", "install", "--force", "--reinstall", str(wheel_path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode == 0:
            return "uv-tool"
        err = (proc.stderr or proc.stdout).strip()
        raise FosError(f"uv tool install failed: {err}")

    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", str(wheel_path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        raise FosError(f"pip install failed: {err}")
    return "pip"


def perform_update(
    cfg: FosConfig,
    cache_dir: Path,
    *,
    home: Path | None = None,
    check_only: bool = False,
    background: bool = False,
    http_transport: Any | None = None,
    installer_fn: Any | None = None,
) -> dict[str, Any]:
    """Check or perform SHA-256 verified CLI self-update from brain and refresh installed AI skills."""
    if cfg.mode != "remote" or not cfg.brain_url:
        return {
            "updated": False,
            "up_to_date": True,
            "mode": "local",
            "local_version": __version__,
            "note": "Running in local brain mode; upgrade via `uv tool install --reinstall .`.",
        }

    lock_path = cache_dir / "update.lock"
    state_path = cache_dir / "update_state.json"

    if not check_only and not _acquire_update_lock(lock_path):
        if background:
            return {"updated": False, "skipped": "locked"}
        raise FosError("Another fos update is already in progress.", hint="Wait a moment and retry.")

    try:
        import httpx

        headers = {"Accept": "application/json"}
        if cfg.token:
            headers["Authorization"] = f"Bearer {cfg.token}"
        base = cfg.brain_url.rstrip("/")

        try:
            with httpx.Client(timeout=15.0, transport=http_transport) as http:
                v_resp = http.get(f"{base}/v1/cli/version", headers=headers)
        except httpx.HTTPError as exc:
            raise OfflineError(f"Brain unreachable at {base}: {exc}") from exc

        if v_resp.status_code in (401, 403):
            raise AuthError(f"Authentication failed ({v_resp.status_code}) against {base}")
        if v_resp.status_code == 404:
            return {
                "updated": False,
                "up_to_date": True,
                "local_version": __version__,
                "remote_version": "",
                "note": "No CLI wheel published on brain yet (run `fosd publish` on M3 Max).",
            }
        v_resp.raise_for_status()
        v_data = v_resp.json()
        remote_version = str(v_data.get("version") or "").strip()

        needs_update = should_auto_update(__version__, remote_version) if background else (bool(remote_version) and remote_version != __version__)
        if check_only or not needs_update:
            return {
                "updated": False,
                "up_to_date": not needs_update,
                "update_available": needs_update,
                "local_version": __version__,
                "remote_version": remote_version,
                "sha256": v_data.get("sha256", ""),
            }

        def _record_failure(msg: str) -> None:
            st = read_update_state(state_path)
            st.failed_version = remote_version
            st.last_attempt_at = int(time.time())
            st.last_error = msg
            save_update_state(state_path, st)

        # Download wheel + streaming SHA-256 verification
        with tempfile.TemporaryDirectory(prefix="fos-update-") as tmp_dir:
            wheel_path = Path(tmp_dir) / f"fos-{remote_version}-py3-none-any.whl"
            try:
                with httpx.Client(timeout=90.0 if background else 180.0, transport=http_transport) as http:
                    with http.stream("GET", f"{base}/v1/cli/download", headers=headers) as dl_resp:
                        if dl_resp.status_code != 200:
                            msg = f"download failed ({dl_resp.status_code})"
                            _record_failure(msg)
                            raise FosError(msg)
                        expected_sha = (dl_resp.headers.get("X-Fos-Sha256") or v_data.get("sha256") or "").strip()
                        hasher = hashlib.sha256()
                        total = 0
                        with wheel_path.open("wb") as out_f:
                            for chunk in dl_resp.iter_bytes(65536):
                                out_f.write(chunk)
                                hasher.update(chunk)
                                total += len(chunk)
            except FosError:
                raise
            except Exception as exc:
                _record_failure(str(exc))
                raise FosError(f"Download failed: {exc}") from exc

            if total < 512:
                msg = f"Downloaded wheel too small ({total} bytes)"
                _record_failure(msg)
                raise FosError(msg)

            got_sha = hasher.hexdigest()
            if expected_sha and got_sha.lower() != expected_sha.lower():
                msg = f"SHA-256 mismatch (got {got_sha}, expected {expected_sha})"
                _record_failure(msg)
                raise FosError(msg)

            try:
                method = installer_fn(wheel_path) if installer_fn is not None else _install_wheel_file(wheel_path)
            except Exception as exc:
                _record_failure(str(exc))
                raise

        now = int(time.time())
        st = read_update_state(state_path)
        st.failed_version = ""
        st.last_error = ""
        st.last_attempt_at = now
        st.updated_from = __version__
        st.updated_to = remote_version
        st.updated_at = now
        save_update_state(state_path, st)

        # Refresh installed AI skills using the NEW CLI so embedded SKILL.md stays in sync
        skills_refreshed = False
        if installer_fn is None:
            env = dict(os.environ)
            env["FOS_NO_AUTOUPDATE"] = "1"
            cmd = _resolve_fos_executable() + ["skill", "install", "--refresh-only", "--json"]
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=10, env=env, check=False)
                skills_refreshed = proc.returncode == 0
            except Exception:
                skills_refreshed = False

        return {
            "updated": True,
            "up_to_date": True,
            "local_version": __version__,
            "remote_version": remote_version,
            "sha256": got_sha,
            "installer": method,
            "skills_refreshed": skills_refreshed,
        }
    finally:
        if not check_only:
            _release_update_lock(lock_path)


def find_published_wheel(dist_dir: Path) -> tuple[str, Path, str] | None:
    """Locate latest published wheel and VERSION in <brain_data_dir>/dist."""
    if not dist_dir.exists():
        return None
    version_file = dist_dir / "VERSION"
    wheels = sorted(dist_dir.glob("fos-*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wheels:
        return None
    wheel = wheels[0]
    if version_file.exists():
        ver = version_file.read_text(encoding="utf-8").strip()
    else:
        parts = wheel.name.split("-")
        ver = parts[1] if len(parts) >= 2 else __version__
    return ver, wheel, sha256_file(wheel)


def publish_cli_wheel(project_root: Path, brain_data_dir: Path) -> dict[str, Any]:
    """Build a wheel from project_root and publish it into <brain_data_dir>/dist for client auto-update."""
    root = Path(project_root).expanduser().resolve()
    if not (root / "pyproject.toml").exists():
        raise UsageError(f"pyproject.toml not found in {root}", hint="Pass --project-dir /path/to/fos")

    dist_dir = Path(brain_data_dir).resolve() / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    for old in dist_dir.glob("fos-*.whl"):
        old.unlink()

    uv_bin = shutil.which("uv")
    if not uv_bin:
        raise FosError("uv executable not found in PATH; cannot build wheel.")

    proc = subprocess.run(
        [uv_bin, "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise FosError(f"uv build failed: {(proc.stderr or proc.stdout).strip()}")

    wheels = sorted(dist_dir.glob("fos-*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wheels:
        raise FosError(f"No wheel found in {dist_dir} after build.")
    wheel = wheels[0]
    parts = wheel.name.split("-")
    ver = parts[1] if len(parts) >= 2 and parts[1] else __version__
    digest = sha256_file(wheel)
    tmp_ver = dist_dir / "VERSION.tmp"
    tmp_ver.write_text(f"{ver}\n", encoding="utf-8")
    os.replace(tmp_ver, dist_dir / "VERSION")
    return {
        "version": ver,
        "wheel": str(wheel),
        "size_bytes": wheel.stat().st_size,
        "sha256": digest,
        "dist_dir": str(dist_dir),
    }
