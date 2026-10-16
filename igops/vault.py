"""Vault: the only place secrets live.

Code, the database, reports and logs hold *references* like `vault://brand/ig/password`,
never values. Backends:

- LocalVault: encrypted file (Fernet, AES-128-CBC + HMAC). The master key comes from
  IGOPS_VAULT_KEY or a key file outside the repo. Used for the demo and until GAJAN's
  vault access arrives.
- HashiCorpVault (KV v2 over HTTP) and OnePasswordVault (official `op` CLI) for the
  vault GAJAN provides. Pick one with [vault] backend in config/brand.toml.

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

    def scan_values(self) -> list[str]:
        """Every secret value, for the leak scan. Never printed."""
        return [e["value"] for e in self._data.values()]


# ---------------------------------------------------------------- real vault adapters

def _path(ref: str) -> str:
    return ref[len(PREFIX):]


class HashiCorpVault:
    """HashiCorp Vault, KV version 2. Address and mount come from config; the token comes
    from the VAULT_TOKEN environment variable, never from a file in the repo."""

    def __init__(self, address: str, mount: str = "secret", token: str | None = None, client=None):
        import httpx
        token = token or os.environ.get("VAULT_TOKEN", "")
        if not token:
            raise VaultError("VAULT_TOKEN is not set")
        register_secret(token)
        self.mount = mount.strip("/")
        self.http = client or httpx.Client(base_url=address.rstrip("/"), headers={"X-Vault-Token": token},
                                           timeout=httpx.Timeout(10.0, connect=5.0))
        self._known: set[str] = set()

    def _req(self, method: str, url: str, **kw):
        r = self.http.request(method, url, **kw)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise VaultError(f"vault {method} {url.split('/')[2]}: HTTP {r.status_code}")
        return r.json() if r.content else {}

    def put(self, ref: str, value: str, *, meta: dict | None = None) -> str:
        self._req("POST", f"/v1/{self.mount}/data/{_path(ref)}", json={"data": {"value": value}})
        if meta:
            self.mark(ref, **meta)
        register_secret(value)
        self._known.add(ref)
        return ref

    def get(self, ref: str) -> str:
        d = self._req("GET", f"/v1/{self.mount}/data/{_path(ref)}")
        if not d:
            raise VaultError(f"no secret at {ref}")
        value = d["data"]["data"]["value"]
        register_secret(value)
        self._known.add(ref)
        return value

    def exists(self, ref: str) -> bool:
        return self._req("GET", f"/v1/{self.mount}/metadata/{_path(ref)}") is not None

    def metadata(self, ref: str) -> dict:
        d = self._req("GET", f"/v1/{self.mount}/metadata/{_path(ref)}")
        if not d:
            return {}
        cm = d["data"].get("custom_metadata") or {}
        meta = {k: json.loads(v) for k, v in cm.items()}
        return {"version": d["data"].get("current_version"), "updated_at": d["data"].get("updated_time"), **meta}

    def mark(self, ref: str, **meta) -> None:
        cur = {k: v for k, v in self.metadata(ref).items() if k not in ("version", "updated_at")}
        cur.update(meta)
        self._req("POST", f"/v1/{self.mount}/metadata/{_path(ref)}",
                  json={"custom_metadata": {k: json.dumps(v) for k, v in cur.items()}})

    def refs(self) -> list[str]:
        return sorted(self._known)

    def scan_values(self) -> list[str]:
        return [self.get(r) for r in self.refs()]


class OnePasswordVault:
    """1Password through its official `op` CLI and a service-account token in
    OP_SERVICE_ACCOUNT_TOKEN. Each ref is an item titled with the ref; metadata is a JSON note."""

    def __init__(self, vault_name: str, op: str = "op", runner=None):
        import subprocess
        self.vault_name, self.op = vault_name, op
        self.run = runner or (lambda args, input=None: subprocess.run(
            [op, *args], input=input, capture_output=True, text=True, timeout=30))
        self._known: set[str] = set()

    def _op(self, *args, input=None):
        p = self.run(list(args), input=input)
        if p.returncode != 0:
            raise VaultError(f"op {args[0]} {args[1] if len(args) > 1 else ''} failed")
        return p.stdout

    def _item(self, ref: str) -> dict | None:
        try:
            return json.loads(self._op("item", "get", ref, "--vault", self.vault_name, "--format", "json"))
        except VaultError:
            return None

    def put(self, ref: str, value: str, *, meta: dict | None = None) -> str:
        item = self._item(ref)
        old_meta = self.metadata(ref) if item else {}
        version = old_meta.get("version", 0) + 1
        note = json.dumps({**{k: v for k, v in old_meta.items() if k != 'version'}, **(meta or {}), "version": version})
        # The value is passed on stdin as a JSON template, never as a command-line argument.
        template = json.dumps({"title": ref, "category": "PASSWORD", "fields": [
            {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "value": value},
            {"id": "notesPlain", "type": "STRING", "purpose": "NOTES", "value": note}]})
        if item:
            self._op("item", "delete", ref, "--vault", self.vault_name)
        self._op("item", "create", "--vault", self.vault_name, "--template", "/dev/stdin", input=template)
        register_secret(value)
        self._known.add(ref)
        return ref

    def get(self, ref: str) -> str:
        value = self._op("read", f"op://{self.vault_name}/{ref}/password").rstrip("\n")
        register_secret(value)
        self._known.add(ref)
        return value

    def exists(self, ref: str) -> bool:
        return self._item(ref) is not None

    def metadata(self, ref: str) -> dict:
        item = self._item(ref)
        if not item:
            return {}
        note = next((f.get("value") for f in item.get("fields", []) if f.get("id") == "notesPlain"), "") or "{}"
        try:
            return json.loads(note)
        except json.JSONDecodeError:
            return {}

    def mark(self, ref: str, **meta) -> None:
        if self.exists(ref):
            self.put(ref, self.get(ref), meta=meta)

    def refs(self) -> list[str]:
        return sorted(self._known)

    def scan_values(self) -> list[str]:
        return [self.get(r) for r in self.refs()]


def open_vault(settings: dict, state_dir: Path):
    backend = (settings or {}).get("backend", "local")
    if backend == "local":
        return LocalVault(Path(state_dir) / "vault.enc")
    if backend == "hashicorp":
        return HashiCorpVault(settings["address"], settings.get("mount", "secret"))
    if backend == "1password":
        return OnePasswordVault(settings["op_vault"])
    raise VaultError(f"unknown vault backend {backend}")
