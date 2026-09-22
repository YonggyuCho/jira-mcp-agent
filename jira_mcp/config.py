"""환경설정 로딩. .env 를 읽되 실제 환경변수가 있으면 그쪽이 이긴다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    site: str
    cloud_id: str
    email: str
    token: str
    auth_mode: str  # auto | gateway | site

    @property
    def gateway_base(self) -> str:
        return f"https://api.atlassian.com/ex/jira/{self.cloud_id}/rest/api/3"

    @property
    def site_base(self) -> str:
        return f"{self.site}/rest/api/3"

    def bases(self) -> list[tuple[str, str]]:
        """시도할 (이름, base_url) 목록. auth_mode 에 따라 순서가 달라진다."""
        gateway = ("gateway", self.gateway_base)
        site = ("site", self.site_base)
        if self.auth_mode == "gateway":
            return [gateway]
        if self.auth_mode == "site":
            return [site]
        return [gateway, site]


def load() -> Config:
    site = os.environ.get("JIRA_SITE", "").rstrip("/")
    cloud_id = os.environ.get("JIRA_CLOUD_ID", "").strip()
    email = os.environ.get("JIRA_EMAIL", "").strip()
    token = os.environ.get("JIRA_API_TOKEN", "").strip()
    mode = os.environ.get("JIRA_AUTH_MODE", "auto").strip().lower()

    missing = [
        name
        for name, value in (
            ("JIRA_SITE", site),
            ("JIRA_CLOUD_ID", cloud_id),
            ("JIRA_EMAIL", email),
            ("JIRA_API_TOKEN", token),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            ".env 에 다음 값이 비어 있습니다: " + ", ".join(missing)
        )
    if mode not in {"auto", "gateway", "site"}:
        raise ConfigError(f"JIRA_AUTH_MODE 는 auto|gateway|site 중 하나여야 합니다 (현재: {mode})")

    return Config(site=site, cloud_id=cloud_id, email=email, token=token, auth_mode=mode)
