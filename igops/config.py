"""Load and validate the brand configuration (config/brand.toml)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Person:
    email: str
    name: str
    roles: dict[str, str]
    test_member: bool = False
    vault_access: list[str] = field(default_factory=list)


@dataclass
class Config:
    mode: str
    brand_key: str
    brand_name: str
    company_domain: str
    website: str
    category: str
    bio: str
    profile_image: str
    owner: str
    escalate_to: str
    signup_due_hours: float
    task_due_hours: float
    stale_access_days: int
    stale_verification_days: int
    company_phones: list[str]
    meta_business_id: str
    meta_ig_user_id: str
    meta_token_ref: str
    people: list[Person]
    proxies: list[dict]
    proxy_policy: dict

    @property
    def simulation(self) -> bool:
        return self.mode != "live"


class ConfigError(Exception):
    pass


ALLOWED_ROLES = {"admin", "content_editor", "insights_viewer", "community_manager", "advertiser"}


def load(path: str | Path = "config/brand.toml") -> Config:
    raw = tomllib.loads(Path(path).read_text())
    b, ops, meta = raw["brand"], raw["ops"], raw.get("meta", {})
    people = [Person(email=p["email"], name=p["name"], roles=p.get("roles", {}),
                     test_member=p.get("test_member", False), vault_access=p.get("vault_access", []))
              for p in raw.get("people", [])]
    cfg = Config(
        mode=raw.get("mode", "simulation"), brand_key=b["key"], brand_name=b["name"],
        company_domain=b["company_domain"].lower(), website=b.get("website", ""), category=b.get("category", ""),
        bio=b.get("bio", ""), profile_image=b.get("profile_image", ""),
        owner=ops["owner"], escalate_to=ops["escalate_to"], signup_due_hours=ops.get("signup_due_hours", 24),
        task_due_hours=ops.get("task_due_hours", 24), stale_access_days=ops.get("stale_access_days", 90),
        stale_verification_days=ops.get("stale_verification_days", 7),
        company_phones=raw.get("company_contacts", {}).get("phones", []),
        meta_business_id=meta.get("business_id", ""), meta_ig_user_id=meta.get("ig_user_id", ""),
        meta_token_ref=meta.get("token_ref", ""), people=people, proxies=raw.get("proxies", []),
        proxy_policy=raw.get("proxy_policy", {}),
    )
    problems = []
    if len(cfg.bio) > 150:
        problems.append(f"bio is {len(cfg.bio)} characters; Instagram allows 150")
    for p in people:
        if not p.email.lower().endswith("@" + cfg.company_domain):
            problems.append(f"{p.email} is not a company address")
        for surface, role in p.roles.items():
            if role not in ALLOWED_ROLES:
                problems.append(f"{p.email}: unknown role {role}")
    if sum(1 for p in people if "admin" in p.roles.values()) > 2:
        problems.append("more than two admins requested; least privilege says one or two")
    for pr in cfg.proxies:
        if not str(pr.get("url_ref", "")).startswith("vault://"):
            problems.append(f"proxy {pr.get('name')} must reference the vault, not a literal URL")
    if problems:
        raise ConfigError("; ".join(problems))
    return cfg
