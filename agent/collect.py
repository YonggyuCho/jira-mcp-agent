"""솔루션 현황 수집과 판정. 여기에는 LLM 이 없다.

판정 규칙은 docs/internal/GPU-LIVE-AUDIT.md 가 원본이다 (비공개). 규칙을 바꾸면 그 문서도 같이 고친다.
LLM 을 쓰지 않는 이유는 매일 같은 입력에 같은 결과가 나와야 하기 때문이다 —
'어제와 달라진 것' 을 신뢰하려면 판정이 흔들리면 안 된다.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from jira_mcp import adf, client, config

# 대상은 .env 로 정한다 (AGENT_PROJECT / AGENT_SOLUTION). 코드에 조직 값을 박지 않는다.
# 솔루션 필드로 고르고 담당자로 좁히지 않는다 — 솔루션 전체 현황을 보는 것이 목적이다.
TARGET_JQL = 'project = {project} AND cf[12473] = "{solution}" ORDER BY key ASC'
# 솔루션 값이 빠진 이슈를 찾으려고 제목으로도 한 번 긁어 대조한다. (에픽을 안 쓸 때만)
TITLE_JQL = 'project = {project} AND summary ~ "{solution}" ORDER BY key ASC'
# 에픽을 쓰면 그 아래 트리를 단계마다 따라 내려간다. JQL 은 트리를 한 번에 못 따라간다.
CHILD_JQL = 'project = {project} AND parent in ({keys}) ORDER BY key ASC'

FIELDS = [
    "summary", "status", "assignee", "reporter", "issuetype", "duedate", "parent",
    "description",        # 설명. ADF(중첩 JSON)라 평문으로 펴서 비었는지 본다
    "customfield_10008",  # Actual start
    "customfield_10009",  # Actual end
    "customfield_10015",  # 시작 날짜
    "customfield_12414",  # 엔지니어 (동명 3개 중 실제로 쓰이는 것)
    "customfield_12427",  # 참조 (동명 4개 중 회의에서 쓰이는 것). 회의는 엔지니어 대신 이걸 본다
    "customfield_12428",  # 엔지니어 지원 방법 (동명 2개 중 실제로 쓰이는 것)
    "customfield_12530",  # 이슈 분류
    "customfield_12594",  # 일정 준수 여부
    "customfield_12604",  # 지연 사유
    "customfield_12473",  # 솔루션
]

# 불일치 판정의 기대값. 규칙 원본은 GPU-LIVE-AUDIT.md 8장.
EXPECT_SUPPORT = "본사"
EXPECT_CATEGORY = "고객요청"          # Jira 값은 띄어쓰기 없음
PROJECT_CHILD_CATEGORY = "솔루션 제품 개선"   # 에픽 직속 '프로젝트' 의 하위 이슈는 이쪽
MEETING_KIND = "회의"                 # 위 예외에서 다시 빠진다
PROJECT_KIND = "프로젝트"
FINE = {"On Time", "Early"}

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
    reporter: str | None = None
    engineer: str | None = None
    cc: str | None = None
    has_description: bool = True
    start_date: str | None = None
    support: str | None = None
    category: str | None = None
    solution: str | None = None
    parent: str | None = None
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


RISK = "위험"
MISMATCH = "불일치"


@dataclass
class Finding:
    """조치가 필요한 건 하나. reason 이 왜 걸렸는지다."""
    issue: Issue
    reason: str
    severity: int  # 낮을수록 급하다
    category: str = RISK  # 위험 / 불일치


@dataclass
class Report:
    today: dt.date
    solution: str
    issues: list[Issue]
    findings: list[Finding] = field(default_factory=list)
    hygiene: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)  # 제외 규칙으로 뺀 건수. 한 줄로만 알린다
    titles: dict[str, str] = field(default_factory=dict)   # 키 → 제목. 화면의 '상위' 열에 쓴다

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
        reporter=_val(f.get("reporter")),
        engineer=_val(f.get("customfield_12414")),
        cc=_val(f.get("customfield_12427")),
        # 공백·줄바꿈만 있는 설명도 '내용 없음' 으로 본다.
        has_description=bool(adf.flatten(f.get("description")).strip()) if f.get("description") else False,
        start_date=_val(f.get("customfield_10015")),
        support=_val(f.get("customfield_12428")),
        category=_val(f.get("customfield_12530")),
        solution=_val(f.get("customfield_12473")),
        parent=(f.get("parent") or {}).get("key"),
        site=site,
    )


def _tree(c: client.JiraClient, project: str, epic: str) -> list[dict]:
    """에픽 아래 전부. 에픽 자신은 넣지 않는다."""
    seen: dict[str, dict] = {}
    level = [epic]
    while level:
        found = []
        for n in range(0, len(level), 50):  # JQL 길이 제한을 피한다
            found += _page(c, CHILD_JQL.format(project=project, keys=", ".join(level[n:n + 50])))
        level = [r["key"] for r in found if r["key"] not in seen]
        seen.update((r["key"], r) for r in found)
    return sorted(seen.values(), key=lambda r: int(r["key"].rsplit("-", 1)[1]))


def _judge(issues: list[Issue], today: dt.date, due_soon: int,
           project_parents: set[str] | None = None,
           skipped: dict[str, int] | None = None) -> list[Finding]:
    """위험 / 불일치를 이유와 함께 남긴다. 규칙 원본은 GPU-LIVE-AUDIT.md 8장.

    project_parents: 이 이슈들의 바로 아래(회의 제외)는 이슈 분류가 '솔루션 제품 개선' 이어야 한다.
    """
    project_parents = project_parents or set()
    skipped = skipped if skipped is not None else {}
    found: list[Finding] = []
    for i in issues:
        left = i.days_left(today)

        # ── 위험 ────────────────────────────────────────────────
        if i.compliance == "Delayed":
            found.append(Finding(i, f"일정 준수 'Delayed' — 지연 사유: {i.delay_reason or '미기재'}", 0))
        elif i.compliance not in FINE:
            if i.compliance == "Not Evaluated" and not i.is_done:
                skipped["진행 중 Not Evaluated"] = skipped.get("진행 중 Not Evaluated", 0) + 1
            else:
                state = "종료" if i.is_done else "진행 중"
                found.append(Finding(i, f"일정 준수 '{i.compliance or '공란'}' ({state})", 1))

        end = _date(i.actual_end)
        if end and i.duedate and end > i.duedate:
            note = f" — 일정 준수는 '{i.compliance}'" if i.compliance in FINE else ""
            found.append(Finding(i, f"Actual end {end} 가 기한 {i.duedate} 보다 늦음{note}", 0))

        if not i.is_done and left is not None and left < 0:
            found.append(Finding(i, f"기한 {abs(left)}일 초과 (진행 중)", 0))
        elif not i.is_done and left is not None and left <= due_soon:
            when = "오늘" if left == 0 else f"{left}일 뒤"
            found.append(Finding(i, f"기한 임박 — {when} ({i.duedate})", 2))

        empty = [name for name, got in (
            ("보고자", i.reporter), ("담당자", i.assignee),
            # 회의(스프린트 회의 등)는 엔지니어가 없고, 대신 참석자를 '참조' 에 적는다.
            ("엔지니어", i.engineer) if i.kind != MEETING_KIND else ("참조", i.cc),
            # 상위 이슈(프로젝트·구축)는 시작 날짜·이슈 분류를 안 채우는 게 정상이다.
            ("시작 날짜", i.start_date if not i.is_parent else "-"),
            ("Actual start", i.actual_start),
            # 진행 중이면 Actual end 가 비어 있는 게 정상이다.
            ("Actual end", i.actual_end if i.is_done else "-"),
            ("솔루션", i.solution), ("엔지니어 지원 방법", i.support),
            ("이슈 분류", i.category if not i.is_parent else "-"), ("기한", i.duedate),
            ("설명", i.has_description),
        ) if not got]
        if empty:
            found.append(Finding(i, "미기입: " + ", ".join(empty), 2))

        # ── 불일치 / 오기입 ─────────────────────────────────────
        # 시작 날짜와 Actual start 는 년월일이 같아야 한다. 시간은 보지 않는다.
        # 둘 중 하나가 비면 건너뛴다 — 빈 칸은 위에서 미기입으로 잡힌다.
        start, actual = _date(i.start_date), _date(i.actual_start)
        if start and actual and start != actual:
            found.append(Finding(i, f"시작 날짜 {start} ≠ Actual start {actual}", 3, MISMATCH))
        if i.support and i.support != EXPECT_SUPPORT:
            found.append(Finding(i, f"엔지니어 지원 방법 '{i.support}' → {EXPECT_SUPPORT}", 3, MISMATCH))
        if i.category:
            want = (PROJECT_CHILD_CATEGORY if i.parent in project_parents and i.kind != MEETING_KIND
                    else EXPECT_CATEGORY)
            if i.category != want:
                found.append(Finding(i, f"이슈 분류 '{i.category}' → {want}", 3, MISMATCH))

    found.sort(key=lambda f: (f.severity, f.issue.duedate or dt.date.max))
    return found


def collect(project: str, solution: str, due_soon_days: int = 3,
            today: dt.date | None = None, epic: str = "") -> Report:
    cfg = config.load()
    c = client.JiraClient(cfg)
    today = today or dt.date.today()
    hygiene: list[str] = []

    def mismatch_line(raw: dict) -> str:
        got = _val(raw["fields"].get("customfield_12473")) or "공란"
        return (f"{raw['key']} 솔루션이 '{got}' 이라 {solution} 집계에서 빠짐 "
                f"— {(raw['fields'].get('summary') or '').strip()[:40]}")

    if epic:
        # 범위 = 에픽 트리 AND 솔루션. 둘 다 필수다 (WORKING-RULES.md 2장).
        tree = _tree(c, project, epic)
        if not tree:
            # 빈 트리를 '이상 없음' 으로 보내면 안 된다. 에픽이 바뀌었거나 옮겨진 경우가 대부분이다.
            # (실제로 하위 이슈가 새 에픽으로 통째로 옮겨진 적이 있다.)
            c.close()
            raise client.JiraError(
                f"에픽 {epic} 아래에 이슈가 하나도 없습니다. 하위 이슈가 다른 에픽으로 "
                f"옮겨졌는지 확인하고 .env 의 AGENT_EPIC 을 고치세요.")
        rows = [r for r in tree
                if solution in (_val(r["fields"].get("customfield_12473")) or "").split(", ")]
        keys = {r["key"] for r in rows}
        # 에픽 안인데 솔루션이 다른 것 = 솔루션 오기입. 범위 밖이라 다른 규칙은 대지 않고
        # 이 불일치 하나만 잡는다. 집계(전체 N건)에는 넣지 않는다.
        strays = [_issue(r, cfg.site) for r in tree if r["key"] not in keys]
        titles = {r["key"]: (r["fields"].get("summary") or "").strip() for r in tree}
        try:
            titles[epic] = (c.get(f"/issue/{epic}", params={"fields": "summary"})["fields"].get("summary") or "").strip()
        except client.JiraError:
            pass  # 제목 하나 때문에 리포트를 멈추지 않는다
        # 에픽 직속 '프로젝트' 의 하위는 이슈 분류가 제품 개선이어야 한다.
        project_parents = {r["key"] for r in tree
                          if (r["fields"].get("parent") or {}).get("key") == epic
                          and (r["fields"].get("issuetype") or {}).get("name") == PROJECT_KIND}
    else:
        rows = _page(c, TARGET_JQL.format(project=project, solution=solution))
        keys = {r["key"] for r in rows}
        # 데이터 위생 — 제목엔 솔루션명이 있는데 솔루션 필드가 다른 것. 집계에서 통째로 빠진다.
        hygiene += [mismatch_line(r)
                    for r in _page(c, TITLE_JQL.format(project=project, solution=solution))
                    if r["key"] not in keys]
        project_parents = set()
        strays = []
        titles = {r["key"]: (r["fields"].get("summary") or "").strip() for r in rows}

    issues = [_issue(r, cfg.site) for r in rows]
    skipped: dict[str, int] = {}
    findings = _judge(issues, today, due_soon_days, project_parents, skipped)
    findings += [Finding(i, f"솔루션 '{i.solution or '공란'}' → {solution} (에픽 안, 집계 제외)",
                         3, MISMATCH) for i in strays]
    report = Report(today=today, solution=solution, issues=issues, findings=findings,
                    hygiene=hygiene, skipped=skipped, titles=titles)
    c.close()
    return report
