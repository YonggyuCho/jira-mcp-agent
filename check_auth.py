"""단계 2 - 접속 검증. MCP 없이 인증만 확인한다.

  python check_auth.py
"""
from __future__ import annotations

import sys

from jira_mcp import client, config


def main() -> int:
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"[설정] {exc}", file=sys.stderr)
        return 2

    print(f"site      : {cfg.site}")
    print(f"cloud_id  : {cfg.cloud_id}")
    print(f"email     : {cfg.email}")
    print(f"token     : {'*' * 8}{cfg.token[-4:]} ({len(cfg.token)}자)")
    print(f"auth_mode : {cfg.auth_mode}")
    print()

    jira = client.JiraClient(cfg)
    try:
        kind, base = jira.resolve_base()
    except client.JiraError as exc:
        print(f"[실패]\n{exc}", file=sys.stderr)
        return 1

    print(f"[OK] 인증 성공 — base = {kind}")
    print(f"     {base}")
    print()

    me = jira.get("/myself")
    print("[내 계정]")
    print(f"  displayName : {me.get('displayName')}")
    print(f"  accountId   : {me.get('accountId')}")
    print(f"  email       : {me.get('emailAddress')}")
    print(f"  timeZone    : {me.get('timeZone')}")
    print(f"  active      : {me.get('active')}")
    print()

    try:
        projects = jira.get("/project/search", params={"maxResults": 10})
        values = projects.get("values", [])
        print(f"[프로젝트] 상위 {len(values)}개 (전체 {projects.get('total', '?')})")
        for p in values:
            print(f"  {p.get('key'):<12} {p.get('name')}")
    except client.JiraError as exc:
        print(f"[프로젝트] 조회 실패 — 범위가 부족할 수 있습니다\n  {exc}")

    print()
    print(f"→ .env 의 JIRA_AUTH_MODE 를 '{kind}' 로 고정해두면 매번 탐색하지 않습니다.")
    jira.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
