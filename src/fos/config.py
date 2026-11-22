"""Configuration management for fos client and fosd brain."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PORT = 7622


def default_fos_home() -> Path:
    env = os.environ.get("FOS_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".fos").resolve()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return f"fos_{secrets.token_urlsafe(32)}"


def default_device_name() -> str:
    return socket.gethostname().split(".")[0].lower() or "local"


@dataclass
class ProviderConfig:
    """Model provider settings for M3 Max brain."""

    embedder: str = "local_hash"  # local_hash | mlx | openai
    embed_model: str = "mlx-community/bge-m3-4bit"
    embed_dim: int = 256
    reranker: str = "lexical"  # lexical | mlx | cli
    rerank_model: str = "mlx-community/Qwen3-Reranker-0.6B-4bit"
    reasoner: str = "heuristic"  # heuristic | mlx | cli | openai
    reasoner_model: str = "mlx-community/Qwen2.5-14B-Instruct-4bit"
    cli_command: str = "claude -p"  # or "agy -p" or "codex exec"
    openai_base_url: str = ""
    openai_api_key_env: str = "OPENAI_API_KEY"


@dataclass
class FosConfig:
    """Unified client & brain configuration stored at <FOS_HOME>/config.json."""

    mode: str = "local"  # "local" (direct brain on this host) | "remote" (talks to fosd)
    device: str = field(default_factory=default_device_name)
    brain_url: str = ""
    token: str = ""  # client bearer token (only on client when mode=="remote")
    token_hash: str = ""  # server SHA-256 hash of bearer token (on brain)
    data_dir: str = ""  # brain data dir; defaults to <FOS_HOME>/brain
    cache_dir: str = ""  # offline cache dir; defaults to <FOS_HOME>/cache
    providers: ProviderConfig = field(default_factory=ProviderConfig)

    def resolved_home(self, home: Path | None = None) -> Path:
        return (home or default_fos_home()).resolve()

    def resolved_data_dir(self, home: Path | None = None) -> Path:
        if self.data_dir:
            return Path(self.data_dir).expanduser().resolve()
        return self.resolved_home(home) / "brain"

    def resolved_cache_dir(self, home: Path | None = None) -> Path:
        if self.cache_dir:
            return Path(self.cache_dir).expanduser().resolve()
        return self.resolved_home(home) / "cache"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FosConfig:
        prov_raw = data.get("providers") or {}
        prov = ProviderConfig(
            **{k: v for k, v in prov_raw.items() if k in ProviderConfig.__dataclass_fields__}
        )
        known = {
            k: v
            for k, v in data.items()
            if k in cls.__dataclass_fields__ and k != "providers"
        }
        return cls(**known, providers=prov)


def config_path(home: Path | None = None) -> Path:
    return (home or default_fos_home()) / "config.json"


def load_config(home: Path | None = None) -> FosConfig:
    path = config_path(home)
    if not path.exists():
        cfg = FosConfig()
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cfg = FosConfig.from_dict(raw)

    # Environment overrides for CI / scripting / containers
    if env_url := os.environ.get("FOS_BRAIN_URL"):
        cfg.brain_url = env_url.rstrip("/")
        cfg.mode = "remote"
    if env_tok := os.environ.get("FOS_TOKEN"):
        cfg.token = env_tok
    if env_tok_hash := os.environ.get("FOS_TOKEN_HASH"):
        cfg.token_hash = env_tok_hash
    if env_data := os.environ.get("FOS_DATA_DIR"):
        cfg.data_dir = env_data
    return cfg


def save_config(cfg: FosConfig, home: Path | None = None) -> Path:
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path
