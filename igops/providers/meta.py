"""Instagram and Meta integration, through Meta's official Graph API only.

What the official API can do for an Instagram business account, and what it cannot:
- CAN read the account's username, name, biography and website. We use this to
  *verify* the profile ("integration-verified").
- CANNOT create accounts, check handle availability, change the bio, enable two-factor,
  or rotate the password. Those are guided human steps by design.
- Business Manager people and asset assignment exist in the Business Management API, but
  the exact permissions depend on how GAJAN's Meta business and system user are set up.
  Until that is confirmed with real access, role changes run as guided human tasks and
  the API is used to verify them where it can.

No proxy is used for Meta calls: they go to graph.facebook.com from our own network with
an official token, so there is no reason to mask or rotate an IP address.
"""
from __future__ import annotations

import httpx

from ..core import RunLog
from ..vault import Vault

GRAPH = "https://graph.facebook.com/v21.0"
FIELDS = "username,name,biography,website,profile_picture_url"


class IntegrationUnavailable(Exception):
    """No token or no access yet: a blocker, not a crash."""


class TransientError(Exception):
    """Worth retrying: timeouts, 429, 5xx."""


class MetaGraph:
    name = "meta-graph"
    simulated = False

    def __init__(self, vault: Vault, token_ref: str, log: RunLog, client: httpx.Client | None = None):
        self.vault, self.token_ref, self.log = vault, token_ref, log
        self.client = client or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0))

    def _token(self) -> str:
        if not self.token_ref or not self.vault.exists(self.token_ref):
            raise IntegrationUnavailable(f"no Meta token in the vault at {self.token_ref}")
        return self.vault.get(self.token_ref)

    def _get(self, path: str, **params) -> dict:
        params["access_token"] = self._token()
        try:
            r = self.client.get(f"{GRAPH}/{path}", params=params)
        except httpx.TimeoutException as exc:
            raise TransientError("Meta API timeout") from exc
        except httpx.TransportError as exc:
            raise TransientError(f"Meta API network error: {type(exc).__name__}") from exc
        if r.status_code == 429 or r.status_code >= 500:
            raise TransientError(f"Meta API {r.status_code}")
        if r.status_code in (401, 403):
            raise IntegrationUnavailable(f"Meta API refused access ({r.status_code}); check token scopes")
        r.raise_for_status()
        return r.json()

    def me(self) -> dict:
        """Cheap token check: who is this token for?"""
        return self._get("me", fields="id,name")

    def instagram_profile(self, ig_user_id: str) -> dict:
        if not ig_user_id:
            raise IntegrationUnavailable("Instagram business account id not known yet")
        return self._get(ig_user_id, fields=FIELDS)

    def page_instagram_account(self, page_id: str) -> str | None:
        data = self._get(page_id, fields="instagram_business_account")
        return (data.get("instagram_business_account") or {}).get("id")


class SimulatedMeta:
    """Stand-in used in simulation mode. Every result it produces is labelled
    'simulated' in reports, so nothing simulated is ever presented as verified fact."""
    name = "meta-simulated"
    simulated = True

    def __init__(self):
        self.accounts: dict[str, dict] = {}
        self.fail_next = 0

    def set_profile(self, ig_user_id: str, **profile):
        self.accounts.setdefault(ig_user_id, {}).update(profile)

    def instagram_profile(self, ig_user_id: str) -> dict:
        if self.fail_next:
            self.fail_next -= 1
            raise TransientError("simulated Meta API 503")
        if ig_user_id not in self.accounts:
            raise IntegrationUnavailable("account not linked yet")
        return dict(self.accounts[ig_user_id], id=ig_user_id)
