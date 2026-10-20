"""Wires the pieces together for one state directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config, load
from .core import RunLog
from .providers.meta import MetaClient, MetaGraph, SimulatedMeta
from .store import Store
from .tasks import Notifier, Tasks
from .vault import Vault, open_vault


@dataclass
class App:
    cfg: Config
    store: Store
    vault: Vault
    log: RunLog
    tasks: Tasks
    meta: MetaClient
    state_dir: Path


def build(
    config_path: str | Path = "config/brand.toml", state_dir: str | Path = "state", echo: bool = True, meta=None
) -> App:
    cfg = load(config_path)
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    log = RunLog(state / "run_log.jsonl", echo=echo)
    store = Store(state / "igops.db")
    vault = open_vault(cfg.vault, state)
    tasks = Tasks(store, log, Notifier(log, vault, f"vault://{cfg.brand_key}/notify/smtp"))
    if meta is None:
        meta = SimulatedMeta() if cfg.simulation else MetaGraph(vault, cfg.meta_token_ref, log)
    return App(cfg, store, vault, log, tasks, meta, state)
