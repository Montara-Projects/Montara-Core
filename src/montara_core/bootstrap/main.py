"""Application composition root and CLI."""
import argparse
from pathlib import Path
import sys

from montara_core.api.http import create_server
from montara_core.application.ports import StorageError
from montara_core.bootstrap.config import ConfigError, load_config
from montara_core.bootstrap.core import build_core


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Montara Core V2 development health")
    parser.add_argument("--config", type=Path, help="explicit external TOML file")
    parser.add_argument("--check-config", action="store_true", help="validate without listening")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.check_config:
        print("Configuration valid")
        return 0
    if config.sqlite_path is not None:
        try:
            build_core(config)
        except StorageError as exc:
            print(f"Storage error: {exc}", file=sys.stderr)
            return 1
    try:
        server = create_server(config.port)
    except OSError:
        print("Startup error: unable to bind local health server", file=sys.stderr)
        return 1
    with server:
        print(f"Montara Core health listening on 127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
