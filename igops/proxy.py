"""Proxy management for the neutral test endpoint.

Policy (also in the decision log):
- PRESERVE the connection for the whole life of an active session. Changing IP inside a
  logged-in session is what platforms treat as account takeover or evasion, and it
  triggers security checks. A session that loses its proxy ends; it is not silently moved.
- ROTATE only between sessions: on a schedule (age or request count), or to fail over
  away from a proxy that failed health checks.
- NEVER rotate in response to a security challenge, block or rate limit. That would be
  evading enforcement. A challenge pauses the session and opens a human task.
- Instagram itself is not reached through these proxies. Meta's official API needs no
  proxy. This module is demonstrated only against a neutral endpoint.

Credentials live in the vault; logs name proxies, never their URLs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from .core import RunLog, iso, redact
from .vault import Vault


class SecurityChallenge(Exception):
    pass


class ProxyDown(Exception):
    pass


@dataclass
class ProxyState:
    name: str
    url_ref: str
    healthy: bool = True
    consecutive_failures: int = 0
    last_latency_ms: int | None = None
    last_checked: str | None = None
    cooldown_until: float = 0.0


@dataclass
class Session:
    account: str
    proxy: ProxyState
    started: float = field(default_factory=time.monotonic)
    requests: int = 0
    paused: bool = False
    client: object = None          # one keep-alive client per session: the same connection throughout


CHALLENGE_MARKERS = ("challenge", "checkpoint", "captcha", "verify_identity", "suspicious")


class ProxyPool:
    def __init__(self, proxies: list[dict], vault: Vault, log: RunLog, policy: dict):
        self.vault, self.log = vault, log
        self.p = {"connect_timeout_s": 3, "read_timeout_s": 5, "max_retries": 3,
                  "unhealthy_after": 2, "cooldown_s": 60, "max_session_requests": 50,
                  "max_session_age_s": 1800, **policy}
        self.proxies = [ProxyState(x["name"], x["url_ref"]) for x in proxies]
        self.sessions: dict[str, Session] = {}
        self.on_challenge = None   # callback(account, detail) -> opens a human task

    # ---------------------------------------------------------------- plumbing
    def _client(self, proxy: ProxyState) -> httpx.Client:
        url = self.vault.get(proxy.url_ref)       # registers the URL with the redactor
        t = httpx.Timeout(self.p["read_timeout_s"], connect=self.p["connect_timeout_s"])
        return httpx.Client(proxy=url, timeout=t, trust_env=False)

    def health_check(self, proxy: ProxyState) -> bool:
        start = time.monotonic()
        try:
            with self._client(proxy) as c:
                r = c.get(self.p["neutral_endpoint"])
            ok = r.status_code == 200
        except httpx.HTTPError as exc:
            ok = False
            err = type(exc).__name__
        proxy.last_checked = iso()
        if ok:
            proxy.healthy, proxy.consecutive_failures = True, 0
            proxy.last_latency_ms = int((time.monotonic() - start) * 1000)
            self.log.write("proxy.health", proxy=proxy.name, ok=True, latency_ms=proxy.last_latency_ms,
                           msg=f"{proxy.name} healthy ({proxy.last_latency_ms} ms)")
        else:
            proxy.consecutive_failures += 1
            if proxy.consecutive_failures >= self.p["unhealthy_after"]:
                proxy.healthy = False
                proxy.cooldown_until = time.monotonic() + self.p["cooldown_s"]
            self.log.write("proxy.health", proxy=proxy.name, ok=False, failures=proxy.consecutive_failures,
                           msg=f"{proxy.name} failed health check ({locals().get('err', 'bad status')}), "
                               f"{proxy.consecutive_failures} in a row{', marked unhealthy' if not proxy.healthy else ''}")
        return proxy.healthy and ok

    def check_all(self) -> dict:
        return {p.name: self.health_check(p) for p in self.proxies}

    def _pick(self, exclude: set[str] = frozenset()) -> ProxyState:
        now = time.monotonic()
        for p in self.proxies:
            if p.name in exclude:
                continue
            if not p.healthy and now >= p.cooldown_until:
                self.health_check(p)               # give it one chance after cooldown
            if p.healthy:
                return p
        raise ProxyDown("no healthy proxy available")

    # ---------------------------------------------------------------- sessions
    def open_session(self, account: str, avoid: str | None = None) -> Session:
        existing = self.sessions.get(account)
        if existing and existing.paused:
            # A challenge pauses the account, not just the connection. Only a person clears it.
            raise SecurityChallenge(f"{account} is paused for human review; automation will not reconnect")
        if existing:
            return existing                        # preserve: an active session keeps its proxy
        try:
            proxy = self._pick({avoid} if avoid else set())
        except ProxyDown:
            proxy = self._pick()                   # only one healthy proxy: reuse it rather than stop
        s = Session(account, proxy, client=self._client(proxy))
        self.sessions[account] = s
        self.log.write("proxy.session_open", account=account, proxy=s.proxy.name,
                       msg=f"session for {account} pinned to {s.proxy.name}")
        return s

    def end_session(self, account: str, reason: str):
        s = self.sessions.pop(account, None)
        if s and s.client:
            s.client.close()
        if s:
            self.log.write("proxy.session_end", account=account, proxy=s.proxy.name, reason=reason,
                           msg=f"session for {account} on {s.proxy.name} ended: {reason}")

    def maybe_rotate(self, account: str) -> bool:
        """Scheduled rotation, only between requests and only when the session is idle."""
        s = self.sessions.get(account)
        if not s:
            return False
        too_old = time.monotonic() - s.started > self.p["max_session_age_s"]
        too_many = s.requests >= self.p["max_session_requests"]
        if not (too_old or too_many):
            return False
        old = s.proxy.name
        self.end_session(account, "scheduled rotation (age or request budget reached)")
        new = self.open_session(account, avoid=old)
        self.log.write("proxy.rotated", account=account, msg=f"rotated {account}: {old} -> {new.proxy.name} (scheduled)")
        return True

    def request(self, account: str, path: str = "") -> dict:
        """One request inside the account's session: bounded retries on the SAME proxy.
        If the proxy dies, the session ends and the caller decides whether to start a new one."""
        s = self.open_session(account)
        url = self.p["neutral_endpoint"].rsplit("/", 1)[0] + "/" + (path or self.p["neutral_endpoint"].rsplit("/", 1)[1])
        last = None
        for attempt in range(1, self.p["max_retries"] + 1):
            try:
                r = s.client.get(url)
                s.requests += 1
                body = r.text.lower()
                if r.status_code in (401, 403, 429) or any(m in body for m in CHALLENGE_MARKERS):
                    s.paused = True
                    detail = f"HTTP {r.status_code} security challenge via {s.proxy.name}"
                    self.log.write("proxy.challenge", account=account, proxy=s.proxy.name,
                                   msg=f"{detail}: PAUSED, no retry, no rotation, human review required")
                    if self.on_challenge:
                        self.on_challenge(account, detail)
                    raise SecurityChallenge(detail)
                r.raise_for_status()
                return {"proxy": s.proxy.name, "status": r.status_code, "body": r.json()}
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = type(exc).__name__
                self.log.write("proxy.retry", account=account, proxy=s.proxy.name, attempt=attempt,
                               msg=f"{s.proxy.name}: {last}, retry {attempt}/{self.p['max_retries']} on the same proxy")
                time.sleep(min(0.2 * 2 ** (attempt - 1), 2))
        s.proxy.healthy = False
        s.proxy.cooldown_until = time.monotonic() + self.p["cooldown_s"]
        self.end_session(account, f"proxy {s.proxy.name} unreachable after {self.p['max_retries']} attempts ({last})")
        raise ProxyDown(f"{s.proxy.name} unreachable")

    def request_with_failover(self, account: str, path: str = "") -> dict:
        """Failover at the session boundary: the dead session ends, a new one starts on a
        healthy proxy, and the request is retried once there. Never used for challenges."""
        try:
            return self.request(account, path)
        except ProxyDown:
            self.log.write("proxy.failover", account=account, msg=f"failing over {account} to a new session")
            return self.request(account, path)

    def clear_challenge(self, account: str, by: str):
        """Called only after a person has resolved the challenge on the official app or site."""
        s = self.sessions.pop(account, None)
        self.log.write("proxy.challenge_cleared", account=account, by=by,
                       msg=f"{account}: challenge cleared by {by}; a fresh session may start")

    def status(self) -> list[dict]:
        return [{"name": p.name, "healthy": p.healthy, "failures": p.consecutive_failures,
                 "latency_ms": p.last_latency_ms, "last_checked": p.last_checked} for p in self.proxies]
