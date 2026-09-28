"""Jira Cloud MCP 서버 (stdio).

도구는 REST 엔드포인트를 1:1로 옮기지 않고, 사용자가 실제로 하려는 일
단위로 묶는다. 응답도 원본 JSON 대신 읽기 좋은 평문으로 눌러서 준다.
"""
from __future__ import annotations

import functools
import re
import unicodedata

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import adf, client, config

mcp = MCPServer(
    "jira",
    instructions=(
        "Atlassian Jira Cloud 읽기 도구. "
        "이슈를 찾아야 하면 search_issues 가 단일 창구다 — JQL 에 project/assignee/updated 중 "
        "최소 하나의 조건이 있어야 하고, 없으면 최근 90일로 자동 제한된다. "
        "검색 결과의 key 로 read_issue 를 부르면 본문·코멘트·첨부까지 읽는다. "
        "일정·솔루션 같은 운영 필드는 read_issue 의 '세부 사항' 에 나오고, "
        "여러 건을 표로 볼 때는 search_issues 의 columns 인자를 쓴다. "
        "JQL 에서 커스텀 필드는 cf[ID] 로 쓴다 — 한글 이름은 Jira 가 해석하지 못하고 "
        "에러 대신 0건을 돌려준다(자주 쓰는 이름은 서버가 알아서 치환한다). "
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

# 이 사이트에서 운영에 쓰는 커스텀 필드. read_issue 의 '세부 사항' 에 찍는다.
# 여기 없는 필드를 보려면 ID 를 추가한다 — 찾는 법은 docs/JIRA-FIELDS.md.
CUSTOM_FIELDS = {
    "customfield_12473": "솔루션",
    "customfield_12487": "고객사명(통합키)",
    "customfield_10008": "Actual start",
    "customfield_10009": "Actual end",
    "customfield_12594": "일정 준수 여부",
    "customfield_12604": "지연 사유",
}

# search_issues(columns=...) 로 덧붙일 수 있는 열. 이름 → Jira 필드.
COLUMNS = {
    "기한": "duedate",
    "생성": "created",
    "우선순위": "priority",
    "보고자": "reporter",
    "솔루션": "customfield_12473",
    "Actual start": "customfield_10008",
    "Actual end": "customfield_10009",
    "일정준수": "customfield_12594",
    "지연사유": "customfield_12604",
}

# JQL 에서 한글 필드명은 해석되지 않는다. 동명 필드가 여럿이라(예: '솔루션' 6개)
# Jira 가 특정하지 못하는데, 에러가 아니라 빈 결과를 준다. 미리 cf[ID] 로 바꾼다.
JQL_ALIASES = {
    "일정 준수 여부": "cf[12594]",
    "고객사명(통합키)": "cf[12487]",
    "지연 사유": "cf[12604]",
    "솔루션": "cf[12473]",
}

# 제약 없는 JQL 은 Jira 가 거부한다. 사용자가 범위를 안 주면 이걸 씌운다.
DEFAULT_BOUND = "updated >= -90d"

# 필드명 뒤에 올 수 있는 JQL 연산자. 치환을 '값' 이 아닌 '필드 자리' 로 한정한다.
_OPS = r"(?:!?=|[<>]=?|!?~|\bis\b|\bnot\b|\bin\b|\bwas\b|\bchanged\b)"


def _jira() -> client.JiraClient:
    global _client
    if _client is None:
        _client = client.JiraClient(config.load())
    return _client


def _alias(jql: str) -> str:
    """한글 커스텀 필드명을 cf[ID] 로 바꾼다.

    연산자가 뒤따를 때만 바꾸므로 검색어(값)로 쓴 같은 낱말은 건드리지 않는다.
    예) text ~ "솔루션" 은 그대로, 솔루션 = "<값>" 은 cf[12473] 으로.
    """
    for name, cf in JQL_ALIASES.items():
        esc = re.escape(name)
        jql = re.sub(rf'"{esc}"\s*(?={_OPS})', f"{cf} ", jql, flags=re.I)
        jql = re.sub(rf'(?<![\w\[]){esc}\s*(?={_OPS})', f"{cf} ", jql, flags=re.I)
    return jql


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


def _val(v) -> str:
    """필드 값에서 표시할 문자열만 뽑는다.

    선택형은 {"value": ...}, 사용자·상태형은 {"name"/"displayName": ...},
    다중선택은 리스트로 온다. 날짜시간은 날짜까지만 남긴다.
    """
    if v is None or v == [] or v == "":
        return "-"
    if isinstance(v, dict):
        return str(v.get("value") or v.get("name") or v.get("displayName") or "-")
    if isinstance(v, list):
        return ", ".join(_val(x) for x in v)
    s = str(v)
    return s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}T", s) else s


def _width(s: str) -> int:
    """터미널 표시 너비. 한글·전각은 두 칸이다."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


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


def search_issues(jql: str, limit: int = 25, columns: str = "") -> str:
    """JQL 로 이슈를 검색한다. 이슈를 '찾는' 모든 경우의 단일 창구.

    Jira Cloud 는 제약 없는 JQL(예: "order by created DESC")을 거부하므로
    project, assignee, updated 같은 조건을 최소 하나는 넣어야 한다.
    조건이 없으면 자동으로 최근 90일 범위가 씌워진다.

    커스텀 필드는 JQL 에서 cf[ID] 로 쓴다. 한글 이름은 Jira 가 해석하지 못하고
    에러 대신 0건을 돌려주니 주의한다. 자주 쓰는 이름(솔루션, 일정 준수 여부,
    지연 사유, 고객사명(통합키))은 서버가 알아서 cf[ID] 로 바꾼다.

    예)
      project = AGI AND status != Done ORDER BY updated DESC
      assignee = currentUser() AND resolution = Unresolved
      project = PROJ AND cf[12473] = "<솔루션명>" ORDER BY key ASC
      project = PROJ AND cf[12594] != "On Time"

    Args:
        jql: JQL 질의문.
        limit: 최대 건수 (1~100).
        columns: 기본 열(key/유형/상태/담당자/수정일/제목) 뒤에 덧붙일 열을
            쉼표로 나열한다. 고를 수 있는 값 — 기한, 생성, 우선순위, 보고자,
            솔루션, Actual start, Actual end, 일정준수, 지연사유.
            예) "기한,Actual end,일정준수"
    """
    limit = max(1, min(int(limit), 100))
    extra = [c.strip() for c in (columns or "").split(",") if c.strip()]
    unknown = [c for c in extra if c not in COLUMNS]
    if unknown:
        raise ToolError(
            "모르는 열: " + ", ".join(unknown)
            + "\n고를 수 있는 열: " + ", ".join(COLUMNS)
        )

    fields = ["summary", "status", "assignee", "priority", "issuetype", "updated"]
    fields += [COLUMNS[c] for c in extra if COLUMNS[c] not in fields]

    resp = _jira().post("/search/jql", json={
        "jql": _bound(_alias(jql)),
        "maxResults": limit,
        "fields": fields,
    })
    issues = resp.get("issues", [])
    if not issues:
        return (
            "결과 없음\n\n"
            "(Jira 는 인식 못 하는 필드명에도 에러 대신 0건을 돌려줍니다. "
            "한글 커스텀 필드명을 썼다면 cf[ID] 로 바꿔 다시 확인하세요 — docs/JIRA-FIELDS.md)"
        )

    rows = []
    for i in issues:
        f = i["fields"]
        rows.append([
            i["key"],
            (f.get("issuetype") or {}).get("name", "-"),
            (f.get("status") or {}).get("name", "-"),
            _person(f.get("assignee")),
            _day(f.get("updated")),
            *[_val(f.get(COLUMNS[c])) for c in extra],
            (f.get("summary") or "").strip(),
        ])

    head = ["key", "유형", "상태", "담당자", "수정일", *extra, "제목"]
    tail = ""
    if not resp.get("isLast", True):
        tail = f"\n\n(더 있음 — limit 을 올리거나 JQL 을 좁히세요. 현재 {len(issues)}건)"
    return (" | ".join(head) + "\n"
            + "\n".join(" | ".join(r) for r in rows) + tail)


def read_issue(key: str) -> str:
    """이슈 하나를 본문·코멘트·첨부까지 읽는다.

    일정 준수 여부, Actual start/end, 솔루션 같은 운영 필드는 '세부 사항' 에 나온다.
    값이 비어 있으면 그 줄은 생략된다.

    Args:
        key: 이슈 키. 예) AGI-906
    """
    key = key.strip().upper()
    issue = _jira().get(
        f"/issue/{key}",
        params={"fields": ",".join(ISSUE_FIELDS + list(CUSTOM_FIELDS))},
    )
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

    # 운영 필드는 따로 묶는다. 이름 길이가 제각각이라 폭을 맞춰서 찍는다.
    details = [(label, _val(f.get(cf)))
               for cf, label in CUSTOM_FIELDS.items()
               if f.get(cf) not in (None, [], "")]
    if details:
        pad = max(_width(k) for k, _ in details)
        out += ["", "── 세부 사항 " + "─" * 37]
        out += [f"  {k}{' ' * (pad - _width(k))} : {v}" for k, v in details]

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


def _explain(exc: client.JiraError) -> str:
    """원본 에러에 '그래서 뭘 해야 하는지' 를 붙인다.

    SDK 는 모르는 예외의 메시지를 지워버린다(UnexpectedToolError). ToolError 로
    올려야 내용이 모델에게 전달되므로, 그 참에 조치까지 적어준다.
    """
    raw = str(exc)
    if "HTTP 404" in raw:
        hint = "이슈 키가 없거나 접근 권한이 없습니다. 키 철자와 프로젝트를 확인하세요."
    elif "HTTP 400" in raw:
        hint = ("요청이 거부됐습니다. JQL 이라면 문법 오류이거나 제약이 없는 질의입니다 — "
                "project, assignee, updated 중 하나는 넣으세요.")
    elif "HTTP 401" in raw or "HTTP 403" in raw:
        hint = "인증 실패 또는 토큰 범위 부족입니다. 토큰 만료와 read:jira-work 범위를 확인하세요."
    elif "HTTP 429" in raw:
        hint = "요청이 너무 잦습니다. 잠시 후 다시 시도하세요."
    elif "연결 실패" in raw:
        hint = "Jira 에 닿지 못했습니다. 네트워크를 확인하세요."
    else:
        hint = "예상 못 한 실패입니다."
    return f"{hint}\n\n원본: {raw}"


def _guard(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except client.JiraError as exc:
            raise ToolError(_explain(exc)) from exc
        except config.ConfigError as exc:
            raise ToolError(f"설정 문제입니다. {exc}") from exc

    return wrapper


for _fn in (whoami, search_issues, read_issue):
    mcp.tool(annotations=READ_ONLY)(_guard(_fn))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
