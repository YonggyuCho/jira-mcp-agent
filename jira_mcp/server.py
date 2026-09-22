"""Jira Cloud MCP 서버 (stdio).

도구는 REST 엔드포인트를 1:1로 옮기지 않고, 사용자가 실제로 하려는 일
단위로 묶는다. 응답도 원본 JSON 대신 읽기 좋은 평문으로 눌러서 준다.
"""
from __future__ import annotations

import re

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import adf, client, config

mcp = MCPServer(
    "jira",
    instructions=(
        "Atlassian Jira Cloud (your-site) 읽기 도구. "
        "이슈를 찾아야 하면 search_issues 가 단일 창구다 — JQL 에 project/assignee/updated 중 "
        "최소 하나의 조건이 있어야 하고, 없으면 최근 90일로 자동 제한된다. "
        "검색 결과의 key 로 read_issue 를 부르면 본문·코멘트·첨부까지 읽는다. "
        "현재 읽기 전용이라 이슈 생성·수정·코멘트 작성은 할 수 없다."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)

_client: client.JiraClient | None = None

ISSUE_FIELDS = [
    "summary", "status", "assignee", "reporter", "priority", "issuetype",
    "project", "created", "updated", "duedate", "resolution", "labels",
    "components", "fixVersions", "parent", "subtasks", "issuelinks",
    "description", "comment", "attachment",
]

# 제약 없는 JQL 은 Jira 가 거부한다. 사용자가 범위를 안 주면 이걸 씌운다.
DEFAULT_BOUND = "updated >= -90d"


def _jira() -> client.JiraClient:
    global _client
    if _client is None:
        _client = client.JiraClient(config.load())
    return _client


def _bound(jql: str) -> str:
    """ORDER BY 만 있는 JQL 에 기본 범위를 씌운다."""
    jql = (jql or "").strip()
    body = re.split(r"\border\s+by\b", jql, maxsplit=1, flags=re.I)[0].strip()
    if body:
        return jql
    order = jql[len(body):].strip()
    return f"{DEFAULT_BOUND} {order}".strip()


def _person(field: dict | None) -> str:
    return (field or {}).get("displayName") or "-"


def _day(ts: str | None) -> str:
    return (ts or "")[:10] or "-"


# --------------------------------------------------------------------------
# 도구
# --------------------------------------------------------------------------

def whoami() -> str:
    """내 Jira 계정 정보를 돌려준다. 접속이 되는지, 누구로 붙었는지 확인할 때 쓴다."""
    me = _jira().get("/myself")
    return "\n".join([
        f"이름      : {me.get('displayName')}",
        f"이메일    : {me.get('emailAddress')}",
        f"accountId : {me.get('accountId')}",
        f"타임존    : {me.get('timeZone')}",
        f"활성      : {me.get('active')}",
    ])


def search_issues(jql: str, limit: int = 25) -> str:
    """JQL 로 이슈를 검색한다. 이슈를 '찾는' 모든 경우의 단일 창구.

    Jira Cloud 는 제약 없는 JQL(예: "order by created DESC")을 거부하므로
    project, assignee, updated 같은 조건을 최소 하나는 넣어야 한다.
    조건이 없으면 자동으로 최근 90일 범위가 씌워진다.

    예)
      project = AGI AND status != Done ORDER BY updated DESC
      assignee = currentUser() AND resolution = Unresolved
      text ~ "쿠버네티스" AND updated >= -14d

    Args:
        jql: JQL 질의문.
        limit: 최대 건수 (1~100).
    """
    limit = max(1, min(int(limit), 100))
    resp = _jira().post("/search/jql", json={
        "jql": _bound(jql),
        "maxResults": limit,
        "fields": ["summary", "status", "assignee", "priority", "issuetype", "updated"],
    })
    issues = resp.get("issues", [])
    if not issues:
        return "결과 없음"

    lines = []
    for i in issues:
        f = i["fields"]
        lines.append(" | ".join([
            i["key"],
            (f.get("issuetype") or {}).get("name", "-"),
            (f.get("status") or {}).get("name", "-"),
            _person(f.get("assignee")),
            _day(f.get("updated")),
            (f.get("summary") or "").strip(),
        ]))

    head = "key | 유형 | 상태 | 담당자 | 수정일 | 제목"
    tail = ""
    if not resp.get("isLast", True):
        tail = f"\n\n(더 있음 — limit 을 올리거나 JQL 을 좁히세요. 현재 {len(issues)}건)"
    return f"{head}\n" + "\n".join(lines) + tail


def read_issue(key: str) -> str:
    """이슈 하나를 본문·코멘트·첨부까지 읽는다.

    Args:
        key: 이슈 키. 예) AGI-906
    """
    key = key.strip().upper()
    issue = _jira().get(f"/issue/{key}", params={"fields": ",".join(ISSUE_FIELDS)})
    f = issue.get("fields", {})

    out = [
        f"{key}  {(f.get('summary') or '').strip()}",
        "",
        f"프로젝트 : {(f.get('project') or {}).get('key')} {(f.get('project') or {}).get('name')}",
        f"유형     : {(f.get('issuetype') or {}).get('name')}",
        f"상태     : {(f.get('status') or {}).get('name')}"
        + (f"  (해결: {(f.get('resolution') or {}).get('name')})" if f.get("resolution") else ""),
        f"우선순위 : {(f.get('priority') or {}).get('name', '-')}",
        f"담당자   : {_person(f.get('assignee'))}",
        f"보고자   : {_person(f.get('reporter'))}",
        f"생성/수정: {_day(f.get('created'))} / {_day(f.get('updated'))}",
    ]
    if f.get("duedate"):
        out.append(f"마감일   : {f['duedate']}")
    if f.get("labels"):
        out.append(f"라벨     : {', '.join(f['labels'])}")
    if f.get("components"):
        out.append(f"컴포넌트 : {', '.join(c['name'] for c in f['components'])}")
    if f.get("fixVersions"):
        out.append(f"픽스버전 : {', '.join(v['name'] for v in f['fixVersions'])}")
    if f.get("parent"):
        p = f["parent"]
        out.append(f"상위      : {p['key']} {p['fields']['summary']}")
    if f.get("subtasks"):
        out.append("하위      : " + ", ".join(s["key"] for s in f["subtasks"]))

    body = adf.flatten(f.get("description"))
    out += ["", "── 본문 " + "─" * 40, body or "(없음)"]

    attachments = f.get("attachment") or []
    if attachments:
        out += ["", "── 첨부 " + "─" * 40]
        out += [f"  {a['filename']}  ({a.get('size', 0):,}B, {_day(a.get('created'))})"
                for a in attachments]

    comments = ((f.get("comment") or {}).get("comments")) or []
    if comments:
        out += ["", f"── 코멘트 {len(comments)}건 " + "─" * 30]
        for c in comments[-10:]:
            out.append(f"\n[{_person(c.get('author'))}  {_day(c.get('created'))}]")
            out.append(adf.flatten(c.get("body"), limit=1500) or "(빈 코멘트)")

    out += ["", f"링크: {config.load().site}/browse/{key}"]
    return "\n".join(out)


for _fn in (whoami, search_issues, read_issue):
    mcp.tool(annotations=READ_ONLY)(_fn)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
