"""Step runner: each step runs at most once to completion, survives interruption, and
retries only what is safe to retry.

A step function returns one of:
  Done(detail)          the step is finished; never runs again
  WaitHuman(task_key)   a person must act; the run stops cleanly and resumes later
  Blocked(reason)       missing access or a platform blocker; recorded on the register
and may raise TransientError (retried with backoff, bounded) or anything else (step failed).
"""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from .core import RunLog, redact
from .providers.meta import TransientError
from .store import Store


@dataclass
class Done:
    detail: str = ""


@dataclass
class WaitHuman:
    task_key: str
    detail: str = ""


@dataclass
class Blocked:
    reason: str


class Interrupted(BaseException):
    """Simulates the process being killed mid-run. BaseException, so no handler swallows it."""


def run_steps(
    store: Store,
    log: RunLog,
    brand: str,
    steps: list[tuple[str, Callable[[], object]]],
    max_retries: int = 3,
    base_delay: float = 0.5,
    sleep=time.sleep,
) -> str:
    """Run steps in order. Returns 'complete', 'waiting_human', 'blocked' or 'failed'."""
    for name, fn in steps:
        st = store.step(brand, name)
        if st and st["status"] == "done":
            continue  # idempotent: finished steps never rerun
        for attempt in range(1, max_retries + 1):
            store.set_step(brand, name, "running", bump=True)
            try:
                result = fn()
            except TransientError as exc:
                if attempt == max_retries:
                    store.set_step(brand, name, "failed", f"gave up after {attempt} attempts: {exc}")
                    log.write("step.failed", step=name, msg=f"{name}: gave up after {attempt} attempts ({exc})")
                    return "failed"
                delay = base_delay * (2 ** (attempt - 1)) * (0.5 + random.random())
                log.write(
                    "step.retry",
                    step=name,
                    attempt=attempt,
                    msg=f"{name}: transient error ({exc}), retry {attempt}/{max_retries - 1} in {delay:.1f}s",
                )
                sleep(delay)
                continue
            except Exception as exc:
                msg = redact(f"{type(exc).__name__}: {exc}")[:300]
                store.set_step(brand, name, "failed", msg)
                log.write("step.failed", step=name, msg=f"{name}: unexpected error, step marked failed ({msg})")
                return "failed"
            if isinstance(result, Done):
                store.set_step(brand, name, "done", result.detail)
                if os.environ.get("IGOPS_KILL_AFTER_STEP") == name:  # test hook: simulate kill -9
                    os._exit(137)
                store.set_blocker(brand, name, None)
                log.write("step.done", step=name, msg=f"{name}: {result.detail}")
                break
            if isinstance(result, WaitHuman):
                store.set_step(brand, name, "waiting_human", result.detail or result.task_key)
                log.write(
                    "step.waiting",
                    step=name,
                    task=result.task_key,
                    msg=f"{name}: waiting on human task {result.task_key}",
                )
                return "waiting_human"
            if isinstance(result, Blocked):
                store.set_step(brand, name, "blocked", result.reason)
                store.set_blocker(brand, name, result.reason)
                log.write("step.blocked", step=name, msg=f"{name}: BLOCKED, {result.reason}")
                return "blocked"
            raise TypeError(f"step {name} returned {result!r}")
    return "complete"
