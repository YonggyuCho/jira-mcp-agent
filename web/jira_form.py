"""'Jira 등록' 화면(데모)의 양식 데이터. Jira 에서 실측해 만든다.

지금은 **데모**다 — 등록 요청을 Jira 로 보내지 않는다. 화면은 보낼 JSON 을 미리 보여 줄 뿐이다.
토큰도 읽기 전용(read:jira-work)이라 보낼 수 없다. 실제 등록을 붙일 때는 docs/WEB.md 의 '다음 단계' 를 본다.

고정값의 근거 (2026-10-08 실측, 대상 솔루션 이슈 37건):
- 솔루션 / 엔지니어 지원 방법 / 고객사명(통합키) / 우선순위 는 100% 같은 값
- 권역은 상위(프로젝트·구축)의 값을 따른다. 비어 있으면 실무 이슈에서 가장 많은 값
- 이슈 분류는 판정 규칙과 같다 — 에픽 직속 '프로젝트' 바로 아래(회의 제외)는 PROJECT_CHILD_CATEGORY, 그 외 EXPECT_CATEGORY
조직 값(에픽 키·솔루션명)은 .env 에서 온다. 사람 목록·계정 ID 는 Jira 에서 그때그때 읽는다(저장소에 안 둔다).
"""
from __future__ import annotations

import os
import threading
import time
from collections import Counter

from jira_mcp import client, config

from agent import collect

CACHE_SEC = 600
_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()

# 화면에서 쓰는 유형. 이 사이트에는 '회의' 가 두 개(일반·하위) 있어 하위 유형(subtask)을 고른다.
TYPES = ["하위 작업", "기술지원", "회의"]
PARENT_TYPES = {collect.PROJECT_KIND, "구축(재구축/증설/형상변경)"}

F = {
    "engineer": "customfield_12414", "cc": "customfield_12427", "support": "customfield_12428",
    "region": "customfield_12472", "solution": "customfield_12473", "route": "customfield_12480",
    "customer": "customfield_12487", "category": "customfield_12530", "start_date": "customfield_10015",
    "actual_start": "customfield_10008",
}
EXTRA = ["reporter", "priority", F["region"], F["route"], F["customer"], F["cc"]]


def _v(x):
    if x in (None, [], ""):
        return None
    if isinstance(x, dict):
        return x.get("value") or x.get("name") or x.get("displayName")
    if isinstance(x, list):
        return [_v(y) for y in x]
    return x


def _people(rows: list[dict]) -> list[dict]:
    """대상 이슈에 담당·보고·엔지니어·참조로 나온 사람. 많이 나온 순."""
    seen: dict[str, str] = {}
    cnt: Counter = Counter()
    for r in rows:
        f = r["fields"]
        users = [f.get("assignee"), f.get("reporter")] + (f.get(F["engineer"]) or []) + (f.get(F["cc"]) or [])
        for u in users:
            if isinstance(u, dict) and u.get("accountId") and u.get("active", True):
                seen[u["accountId"]] = u.get("displayName") or u["accountId"]
                cnt[u["accountId"]] += 1
    # 등록 양식의 담당자·엔지니어·참조 후보에서 뺄 사람 (.env, 쉼표 구분, 표시 이름 기준).
    # 이름은 조직 정보라 코드에 두지 않는다.
    skip = {x.strip() for x in os.environ.get("WEB_FORM_EXCLUDE_PEOPLE", "").split(",") if x.strip()}
    return [{"id": a, "name": seen[a], "n": cnt[a]} for a, _ in cnt.most_common() if seen[a] not in skip]


def _build() -> dict:
    cfg = config.load()
    c = client.JiraClient(cfg)
    project = os.environ.get("AGENT_PROJECT", "").strip()
    solution = os.environ.get("AGENT_SOLUTION", "").strip()
    epic = os.environ.get("AGENT_EPIC", "").strip()
    try:
        saved = list(collect.FIELDS)
        collect.FIELDS[:] = list(dict.fromkeys(saved + EXTRA))
        try:
            tree = collect._tree(c, project, epic) if epic else []
        finally:
            collect.FIELDS[:] = saved
        rows = [r for r in tree if solution in (_v(r["fields"].get(F["solution"])) or [])]

        # 유형 id (회의는 subtask 쪽)
        types = c.get(f"/issue/createmeta/{project}/issuetypes").get("issueTypes", [])
        type_ids = {}
        for name in TYPES:
            cands = [t for t in types if t["name"] == name]
            pick = next((t for t in cands if t.get("subtask")), cands[0] if cands else None)
            if pick:
                type_ids[name] = pick["id"]

        # 선택지 (요청 경로·권역) — 기술지원 유형의 메타에서
        options: dict[str, list[str]] = {}
        if "기술지원" in type_ids:
            meta = c.get(f"/issue/createmeta/{project}/issuetypes/{type_ids['기술지원']}",
                         params={"maxResults": 100})
            for fm in meta.get("fields") or meta.get("values") or []:
                if fm["fieldId"] in (F["route"], F["region"]):
                    options[fm["fieldId"]] = [a.get("value") for a in fm.get("allowedValues") or []]

        work = [r for r in rows if r["fields"]["issuetype"]["name"] not in PARENT_TYPES]
        region_mode = Counter(x for r in work for x in (_v(r["fields"].get(F["region"])) or [])).most_common(1)
        default_region = region_mode[0][0] if region_mode else None

        parents = []
        for r in rows:
            f = r["fields"]
            if f["issuetype"]["name"] not in PARENT_TYPES or (f.get("parent") or {}).get("key") != epic:
                continue
            kids = [k for k in work if (k["fields"].get("parent") or {}).get("key") == r["key"]]
            route_mode = Counter(_v(k["fields"].get(F["route"])) for k in kids if _v(k["fields"].get(F["route"])))
            parents.append({
                "key": r["key"], "title": (f.get("summary") or "").strip(), "kind": f["issuetype"]["name"],
                "region": (_v(f.get(F["region"])) or [None])[0] or default_region,
                "customer": (_v(f.get(F["customer"])) or [None])[0],
                "route": route_mode.most_common(1)[0][0] if route_mode else "고객",
                "default_type": "하위 작업" if f["issuetype"]["name"] == collect.PROJECT_KIND else "기술지원",
                "children": len(kids),
            })
        parents.sort(key=lambda p: (p["kind"] != collect.PROJECT_KIND, p["key"]))

        people = _people(rows)
        # 엔지니어 기본값 = 실무 이슈에서 가장 많이 나온 엔지니어 조합
        combo = Counter(tuple(sorted(u["accountId"] for u in (r["fields"].get(F["engineer"]) or [])))
                        for r in work if r["fields"].get(F["engineer"])).most_common(1)
        me = c.get("/myself")
        epic_title = (c.get(f"/issue/{epic}", params={"fields": "summary"})["fields"].get("summary") or "").strip() if epic else ""
        fixed = {
            "solution": solution,
            "support": collect.EXPECT_SUPPORT,
            "priority": Counter(_v(r["fields"].get("priority")) for r in rows).most_common(1)[0][0] if rows else None,
        }
        return {
            "ok": True, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "project": project, "epic": epic,
            "epic_title": epic_title,
            "types": type_ids, "parents": parents, "people": people,
            "default_engineers": list(combo[0][0]) if combo else [],
            "me": {"id": me.get("accountId"), "name": me.get("displayName")},
            "fixed": fixed, "options": {"route": options.get(F["route"], []), "region": options.get(F["region"], [])},
            "category": {"project_child": collect.PROJECT_CHILD_CATEGORY, "default": collect.EXPECT_CATEGORY,
                         "meeting_kind": collect.MEETING_KIND, "project_kind": collect.PROJECT_KIND},
            "fields": F, "basis": len(rows),
        }
    finally:
        c.close()


def form_meta(refresh: bool = False) -> dict:
    """10분 캐시. Jira 를 못 읽으면 ok=False 와 오류 문구."""
    with _lock:
        if not refresh and _cache["data"] and time.time() - _cache["at"] < CACHE_SEC:
            return _cache["data"]
        try:
            data = _build()
        except Exception as exc:  # 화면은 떠야 한다 — 오류를 화면에 보여 준다
            return {"ok": False, "error": str(exc)[:300]}
        _cache.update(at=time.time(), data=data)
        return data
