"""Vault: the only place secrets live.

Code, the database, reports and logs hold *references* like `vault://brand/ig/password`,
never values. Two backends:

- LocalVault: encrypted file (Fernet, AES-128-CBC + HMAC). The master key comes from
  IGOPS_VAULT_KEY or a key file outside the repo. Used for the demo and until GAJAN's
  vault access arrives.
- An adapter for the provided vault (1Password, Bitwarden, HashiCorp...) implements the
  same four methods; see README "Connecting the real vault".

Secret values returned by `get()` are registered with the redactor immediately.
"""
from __future__ import annotations

import json
import os
import secrets
import string
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from .core import iso, register_secret

PREFIX = "vault://"


class VaultError(Exception):
    pass


class Vault(Protocol):
    def put(self, ref: str, value: str, *, meta: dict | None = None) -> str: ...
    def get(self, ref: str) -> str: ...
    def exists(self, ref: str) -> bool: ...
    def metadata(self, ref: str) -> dict: ...
    def mark(self, ref: str, **meta) -> None: ...


def ref(*parts: str) -> str:
    return PREFIX + "/".join(p.strip("/") for p in parts)


def generate_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#%^*-_=+"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#%^*-_=+" for c in pw)):
            return pw


class LocalVault:
    def __init__(self, path: Path, key: bytes | None = None):
        self.path = Path(path)
        key = key or os.environ.get("IGOPS_VAULT_KEY", "").encode() or None
        key_file = self.path.with_suffix(".key")
        if key is None:
            if key_file.exists():
                key = key_file.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.write_bytes(key)
                os.chmod(key_file, 0o600)
        self._f = Fernet(key)
        self._data: dict = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._f.decrypt(self.path.read_bytes()))
        except InvalidToken as exc:
            raise VaultError("vault key does not match the vault file") from exc

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self._f.encrypt(json.dumps(self._data).encode()))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)   # atomic: a crash never leaves a half-written vault

    def put(self, ref: str, value: str, *, meta: dict | None = None) -> str:
        if not ref.startswith(PREFIX):
            raise VaultError("refs must start with vault://")
        prev = self._data.get(ref)
        version = (prev["version"] + 1) if prev else 1
        history = (prev.get("history", []) + [{"version": prev["version"], "retired_at": iso()}]) if prev else []
        self._data[ref] = {"value": value, "version": version, "updated_at": iso(),
                           "meta": {**(prev or {}).get("meta", {}), **(meta or {})}, "history": history}
        self._save()
        register_secret(value)
        return ref

    def get(self, ref: str) -> str:
        if ref not in self._data:
            raise VaultError(f"no secret at {ref}")
        value = self._data[ref]["value"]
        register_secret(value)
        return value

    def exists(self, ref: str) -> bool:
        return ref in self._data

    def metadata(self, ref: str) -> dict:
        """Everything except the value: safe to show in reports."""
        e = self._data.get(ref)
        if not e:
            return {}
        return {"version": e["version"], "updated_at": e["updated_at"], **e.get("meta", {})}

    def mark(self, ref: str, **meta) -> None:
        if ref in self._data:
            self._data[ref]["meta"].update(meta)
            self._save()

    def refs(self) -> list[str]:
        return sorted(self._data)
