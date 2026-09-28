"""솔루션 현황 수집과 판정. 여기에는 LLM 이 없다.

판정 규칙은 docs/GPU-LIVE-AUDIT.md 가 원본이다. 규칙을 바꾸면 그 문서도 같이 고친다.
LLM 을 쓰지 않는 이유는 매일 같은 입력에 같은 결과가 나와야 하기 때문이다 —
'어제와 달라진 것' 을 신뢰하려면 판정이 흔들리면 안 된다.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from jira_mcp import client, config

# 대상은 .env 로 정한다 (AGENT_PROJECT / AGENT_SOLUTION). 코드에 조직 값을 박지 않는다.
# 솔루션 필드로 고르고 담당자로 좁히지 않는다 — 솔루션 전체 현황을 보는 것이 목적이다.
TARGET_JQL = 'project = {project} AND cf[12473] = "{solution}" ORDER BY key ASC'
# 솔루션 값이 빠진 이슈를 찾으려고 제목으로도 한 번 긁어 대조한다.
TITLE_JQL = 'project = {project} AND summary ~ "{solution}" ORDER BY key ASC'

FIELDS = [
    "summary", "status", "assignee", "issuetype", "duedate",
    "customfield_10008",  # Actual start
    "customfield_10009",  # Actual end
    "customfield_12594",  # 일정 준수 여부
    "customfield_12604",  # 지연 사유
    "customfield_12473",  # 솔루션
]

# 화면 표시명 기준. JQL 로는 상태를 거를 수 없어(표시명 != JQL 상태명) 코드에서 판단한다.
DONE = {"종료", "완료", "완료(Done)", "Done", "해결됨"}
# 장기 상위 이슈. 미평가가 정상이라 보고하지 않는다.
PARENT_KINDS = {"프로젝트", "구축(재구축/증설/형상변경)"}


@dataclass
class Issue:
    key: str
    summary: str
    status: str
    kind: str
    assignee: str | None
    duedate: dt.date | None
    actual_start: str | None
    actual_end: str | None
    compliance: str | None
    delay_reason: str | None
    site: str = ""

    @property
    def is_done(self) -> bool:
        return self.status in DONE

    @property
    def is_parent(self) -> bool:
        return self.kind in PARENT_KINDS

    @property
    def url(self) -> str:
        return f"{self.site}/browse/{self.key}"

    def days_left(self, today: dt.date) -> int | None:
        return None if self.duedate is None else (self.duedate - today).days


@dataclass
class Finding:
    """조치가 필요한 건 하나. reason 이 왜 걸렸는지다."""
    issue: Issue
    reason: str
    severity: int  # 낮을수록 급하다


@dataclass
class Report:
    today: dt.date
    solution: str
    issues: list[Issue]
    findings: list[Finding] = field(default_factory=list)
    hygiene: list[str] = field(default_factory=list)

    @property
    def open_issues(self) -> list[Issue]:
        return [i for i in self.issues if not i.is_done]

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            out[i.status] = out.get(i.status, 0) + 1
        return out


def _val(v):
    """선택·사용자·다중선택·날짜시간을 표시 문자열로 눌러준다."""
    if v in (None, [], ""):
        return None
    if isinstance(v, dict):
        return v.get("value") or v.get("name") or v.get("displayName")
    if isinstance(v, list):
        return ", ".join(str(_val(x)) for x in v if _val(x) is not None) or None
    s = str(v)
    return s[:10] if len(s) > 10 and s[4] == "-" and "T" in s else s


def _date(s: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def _page(c: client.JiraClient, jql: str) -> list[dict]:
    """/search/jql 은 startAt 이 아니라 nextPageToken 으로 넘긴다."""
    rows: list[dict] = []
    token = None
    while True:
        body = {"jql": jql, "maxResults": 100, "fields": FIELDS}
        if token:
            body["nextPageToken"] = token
        resp = c.post("/search/jql", json=body)
        rows += resp.get("issues", [])
        token = resp.get("nextPageToken")
        if resp.get("isLast", True) or not token:
            return rows


def _issue(raw: dict, site: str) -> Issue:
    f = raw["fields"]
    return Issue(
        key=raw["key"],
        summary=(f.get("summary") or "").strip(),
        status=(f.get("status") or {}).get("name", "-"),
        kind=(f.get("issuetype") or {}).get("name", "-"),
        assignee=_val(f.get("assignee")),
        duedate=_date(f.get("duedate")),
        actual_start=_val(f.get("customfield_10008")),
        actual_end=_val(f.get("customfield_10009")),
        compliance=_val(f.get("customfield_12594")),
        delay_reason=_val(f.get("customfield_12604")),
        site=site,
    )


def _judge(issues: list[Issue], today: dt.date, due_soon: int) -> list[Finding]:
    """조치가 필요한 것만 남긴다. Early 와 상위 이슈 미평가는 보고하지 않는다."""
    found: list[Finding] = []
    for i in issues:
        left = i.days_left(today)

        if i.compliance == "Delayed":
            found.append(Finding(i, f"Delayed — 사유: {i.delay_reason or '미기재'}", 0))

        if not i.is_done and left is not None and left < 0:
            found.append(Finding(i, f"기한 {abs(left)}일 초과 (미종료)", 1))
        elif not i.is_done and left is not None and left <= due_soon:
            when = "오늘" if left == 0 else f"{left}일 뒤"
            found.append(Finding(i, f"기한 임박 — {when} ({i.duedate})", 2))

        if not i.is_done and not i.assignee:
            found.append(Finding(i, "담당자 없음", 2))

        # 끝났는데 일정 준수 여부가 안 채워진 것. 상위 이슈는 제외 규칙 대상이라 건너뛴다.
        if i.is_done and not i.is_parent and i.compliance in (None, "Not Evaluated"):
            found.append(Finding(i, f"종료인데 일정 준수 여부가 '{i.compliance or '공란'}'", 3))

    found.sort(key=lambda f: (f.severity, f.issue.duedate or dt.date.max))
    return found


def collect(project: str, solution: str, due_soon_days: int = 3,
            today: dt.date | None = None) -> Report:
    cfg = config.load()
    c = client.JiraClient(cfg)
    today = today or dt.date.today()

    target = TARGET_JQL.format(project=project, solution=solution)
    title = TITLE_JQL.format(project=project, solution=solution)

    issues = [_issue(r, cfg.site) for r in _page(c, target)]
    report = Report(today=today, solution=solution, issues=issues,
                    findings=_judge(issues, today, due_soon_days))

    # 데이터 위생 — 제목엔 솔루션명이 있는데 솔루션 필드가 다른 것. 집계에서 통째로 빠진다.
    known = {i.key for i in issues}
    for raw in _page(c, title):
        if raw["key"] in known:
            continue
        got = _val(raw["fields"].get("customfield_12473")) or "공란"
        report.hygiene.append(
            f"{raw['key']} 솔루션이 '{got}' 이라 {solution} 집계에서 빠짐 "
            f"— {(raw['fields'].get('summary') or '').strip()[:40]}"
        )

    c.close()
    return report
