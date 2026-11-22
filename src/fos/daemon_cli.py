"""Daemon CLI entry point (`fosd`) for M3 Max always-on Brain server."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fos import __version__
from fos.config import DEFAULT_PORT, generate_token, hash_token, load_config, save_config
from fos.errors import ExitCode, FosError, UsageError


class _StrictParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


def run_daemon_cli(argv: list[str] | None = None) -> int:
    as_json = False
    try:
        parser = _StrictParser(prog="fosd", description="FlowOS M3 Max Brain daemon")
        parser.add_argument("--version", action="version", version=f"fosd {__version__}")
        parser.add_argument("--home", type=str, default=None, help="Override FOS_HOME directory")
        sub = parser.add_subparsers(dest="command", required=True)

        p_init = sub.add_parser("init", help="Initialize brain data dir and generate SHA-256 hashed Bearer token")
        p_init.add_argument("--data-dir", type=str, default=None)
        p_init.add_argument("--token", type=str, default=None, help="Optional custom token (auto-generated if omitted)")
        p_init.add_argument("--json", action="store_true")

        p_serve = sub.add_parser("serve", help="Start fosd HTTP server")
        p_serve.add_argument("--host", type=str, default="127.0.0.1")
        p_serve.add_argument("--port", type=int, default=DEFAULT_PORT)
        p_serve.add_argument("--data-dir", type=str, default=None)
        p_serve.add_argument("--no-auth", action="store_true", help="Disable Bearer auth (local loopback dev only)")

        p_rebuild = sub.add_parser("rebuild", help="Rebuild SQLite snapshot from ledger/*.jsonl")
        p_rebuild.add_argument("--json", action="store_true")

        p_publish = sub.add_parser("publish", help="Build & publish fos CLI wheel to <brain>/dist for client auto-update")
        p_publish.add_argument("--project-dir", type=str, default=".", help="Path to fos repo root (default: cwd)")
        p_publish.add_argument("--json", action="store_true")

        args = parser.parse_args(argv)
        as_json = bool(getattr(args, "json", False))
        home = Path(args.home).expanduser().resolve() if args.home else None

        if args.command == "init":
            cfg = load_config(home)
            cfg.mode = "local"
            if args.data_dir:
                cfg.data_dir = str(Path(args.data_dir).expanduser().resolve())
            raw_token = args.token or generate_token()
            cfg.token_hash = hash_token(raw_token)
            cfg_path = save_config(cfg, home)

            from fos.engine import BrainEngine

            engine = BrainEngine.from_config(cfg, home=home)
            res = {
                "config_path": str(cfg_path),
                "data_dir": str(engine.data_dir),
                "snapshot_version": engine.snapshots.current_version(),
                "token": raw_token,
                "token_hash": cfg.token_hash,
                "note": "Save this plaintext token once; only its SHA-256 hash is stored on the brain.",
            }
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return int(ExitCode.OK)

        if args.command == "rebuild":
            from fos.engine import BrainEngine

            cfg = load_config(home)
            engine = BrainEngine.from_config(cfg, home=home)
            res = engine.rebuild_from_ledger()
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return int(ExitCode.OK)

        if args.command == "publish":
            from fos.update import publish_cli_wheel

            cfg = load_config(home)
            res = publish_cli_wheel(Path(args.project_dir), cfg.resolved_data_dir(home))
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return int(ExitCode.OK)

        if args.command == "serve":
            import uvicorn

            from fos.engine import BrainEngine
            from fos.server import create_app

            cfg = load_config(home)
            if args.data_dir:
                cfg.data_dir = str(Path(args.data_dir).expanduser().resolve())
            engine = BrainEngine.from_config(cfg, home=home)
            tok_hash = "" if args.no_auth else cfg.token_hash
            app = create_app(engine=engine, token_hash=tok_hash, home=home)
            uvicorn.run(app, host=args.host, port=args.port, log_level="info")
            return int(ExitCode.OK)

        raise UsageError(f"Unknown command: {args.command}")
    except FosError as exc:
        if as_json:
            print(json.dumps({"error": str(exc), "code": int(exc.exit_code)}, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else int(ExitCode.ERROR)


def main() -> None:
    raise SystemExit(run_daemon_cli())


if __name__ == "__main__":
    main()
