#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_INFRAOPS_ROOT = Path("/home/kiyohisa/prod/mcp/InfraOps_MCP")
DEFAULT_CREDENTIAL_NAME = "dstack-vast-api-key"
DEFAULT_CREDENTIAL_FD = 3
MAX_CREDENTIAL_BYTES = 4096


def _load_credential() -> bytes:
    infraops_root = Path(os.environ.get("DSTACK_INFRAOPS_ROOT", DEFAULT_INFRAOPS_ROOT))
    sys.path.insert(0, str(infraops_root / "src"))
    from credential_manager import get_credential

    name = os.environ.get("DSTACK_VAST_CREDENTIAL_NAME", DEFAULT_CREDENTIAL_NAME)
    value = get_credential(name)
    if not value or len(value) > MAX_CREDENTIAL_BYTES:
        raise RuntimeError("Vast credential size is invalid")
    return value


def _install_credential_pipe(value: bytes) -> int:
    target_fd = int(os.environ.get("DSTACK_VAST_CREDENTIAL_FD", DEFAULT_CREDENTIAL_FD))
    if target_fd < 3:
        raise RuntimeError("credential fd must be at least 3")

    read_fd, write_fd = os.pipe()
    try:
        written = os.write(write_fd, value)
        if written != len(value):
            raise RuntimeError("credential pipe write was incomplete")
    finally:
        os.close(write_fd)

    if read_fd != target_fd:
        os.dup2(read_fd, target_fd, inheritable=True)
        os.close(read_fd)
    else:
        os.set_inheritable(target_fd, True)
    return target_fd


def main() -> int:
    try:
        value = _load_credential()
        _install_credential_pipe(value)
    except Exception:
        print("dstack startup credential preparation failed", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parents[1]
    executable = os.environ.get(
        "DSTACK_EXECUTABLE",
        str(repo_root / ".venv" / "bin" / "dstack"),
    )
    os.execv(executable, [executable, *sys.argv[1:]])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
