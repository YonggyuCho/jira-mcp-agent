"""Jira Cloud REST v3 클라이언트.

scoped 토큰은 api.atlassian.com gateway 로만, 범위 없는 클래식 토큰은
사이트 URL 로만 동작한다. 어느 쪽인지 모르는 상태를 기본으로 두고
첫 호출 때 실제로 되는 쪽을 찾아 고정한다.
"""
from __future__ import annotations

import logging

import httpx

from . import config


# stdio 전송에서 stdout 은 프로토콜 전용 채널이다. httpx 의 요청 로그는
# stderr 로 가지만 어차피 소음이라 끈다.
logging.getLogger("httpx").setLevel(logging.WARNING)


class JiraError(RuntimeError):
    pass


class JiraClient:
    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self._http = httpx.Client(
            auth=(cfg.email, cfg.token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
            follow_redirects=True,
        )
        self._base: str | None = None
        self._base_kind: str | None = None

    # ---- base URL 결정 -------------------------------------------------

    def resolve_base(self) -> tuple[str, str]:
        """(kind, base_url) 을 돌려준다. 이미 정해졌으면 그대로."""
        if self._base:
            return self._base_kind, self._base  # type: ignore[return-value]

        attempts: list[str] = []
        for kind, base in self.cfg.bases():
            try:
                r = self._http.get(f"{base}/myself", params={"expand": ""})
            except httpx.HTTPError as exc:
                attempts.append(f"{kind}: 연결 실패 ({exc.__class__.__name__}: {exc})")
                continue
            if r.status_code == 200:
                self._base_kind, self._base = kind, base
                return kind, base
            attempts.append(f"{kind}: HTTP {r.status_code} {_short(r.text)}")

        raise JiraError(
            "어느 base URL 로도 인증에 성공하지 못했습니다.\n  " + "\n  ".join(attempts)
        )

    @property
    def base_kind(self) -> str | None:
        return self._base_kind

    # ---- 요청 ----------------------------------------------------------

    def request(self, method: str, path: str, **kwargs) -> dict:
        _, base = self.resolve_base()
        url = f"{base}{path}"
        try:
            r = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise JiraError(f"{method} {path} 연결 실패: {exc}") from exc
        if r.status_code >= 400:
            raise JiraError(f"{method} {path} → HTTP {r.status_code}: {_short(r.text)}")
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError as exc:
            raise JiraError(f"{method} {path} 응답이 JSON 이 아닙니다: {_short(r.text)}") from exc

    def get(self, path: str, **kwargs) -> dict:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> dict:
        return self.request("POST", path, **kwargs)

    def close(self) -> None:
        self._http.close()


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")
