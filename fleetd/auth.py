"""Shared bearer token. Stored in the macOS Keychain (service fleet.token)."""

from __future__ import annotations

import hmac
import os
import subprocess

from fastapi import HTTPException, Request

KEYCHAIN_SERVICE = "fleet.token"
KEYCHAIN_ACCOUNT = "fleet"
COOKIE = "fleet_token"
WRITE_HEADER = "X-Fleet-Request"


def load_token(config_token: str | None = None) -> str:
    """Env var > config file > Keychain."""
    tok = os.environ.get("FLEET_TOKEN") or config_token
    if tok:
        return tok
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
             "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        tok = out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        tok = ""
    if not tok:
        raise SystemExit("fleetd: no token. Run `bin/fleet token init` (or set FLEET_TOKEN).")
    return tok


def extract(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return request.cookies.get(COOKIE)


def valid(got: str | None, token: str) -> bool:
    return bool(got) and hmac.compare_digest(got.encode(), token.encode())


def check(request: Request, token: str) -> None:
    if not valid(extract(request), token):
        raise HTTPException(status_code=401, detail="unauthorized")
