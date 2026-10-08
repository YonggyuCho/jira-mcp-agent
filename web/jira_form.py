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
    "actual_start": "customfield_10008", "actual_end": "customfield_10009",
    "compliance": "customfield_12594", "delay": "customfield_12604",
}
# 하위 작업·기술지원 워크플로 전환 id (2026-10-08 실측, docs/JIRA-FIELDS.md 7장).
# 전환이 Actual start/end 를 '지금' 으로 덮어쓰므로 실제 값은 전환 뒤에 PUT 으로 다시 넣는다.
TRANSITIONS = {"start": "2", "close": "3"}
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
                if fm["fieldId"] in (F["route"], F["region"], F["delay"], F["compliance"], F["category"]):
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
            "route": "고객",  # 요청 경로는 '고객' 고정 (사용자 요구 R41)
            "priority": Counter(_v(r["fields"].get("priority")) for r in rows).most_common(1)[0][0] if rows else None,
        }
        return {
            "ok": True, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "project": project, "epic": epic,
            "epic_title": epic_title,
            "types": type_ids, "parents": parents, "people": people,
            "default_engineers": list(combo[0][0]) if combo else [],
            "me": {"id": me.get("accountId"), "name": me.get("displayName")},
            "fixed": fixed, "options": {"route": options.get(F["route"], []), "region": options.get(F["region"], []),
                                         "delay": options.get(F["delay"], []), "compliance": options.get(F["compliance"], []),
                                         "category": options.get(F["category"], [])},
            "transitions": TRANSITIONS,
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


# ── 검증 (dry-run) ─────────────────────────────────────────────────────────
# Jira 에는 '등록 미리 시험' API 가 없다. 그래서 읽기 API 로 실제 등록 때 실패할 지점을 하나씩 확인한다.
# 아무것도 쓰지 않는다.

def _sample(c, project: str, type_id: str, cat: str) -> str | None:
    """같은 유형에서 상태 범주가 cat(new / indeterminate / done)인 이슈 하나."""
    names = {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}
    r = c.post("/search/jql", json={"jql": f'project = {project} AND issuetype = {type_id} '
                                            f'AND statusCategory = "{names[cat]}" ORDER BY updated DESC',
                                     "maxResults": 1, "fields": ["status"]})
    iss = r.get("issues") or []
    return iss[0]["key"] if iss else None


def validate(steps: list[dict]) -> list[dict]:
    """steps = 화면이 만든 단계별 요청. 결과: [{stage, level(ok/warn/bad), msg}]"""
    out: list[dict] = []
    add = lambda stage, level, msg: out.append({"stage": stage, "level": level, "msg": msg})  # noqa: E731
    meta = form_meta()
    if not meta.get("ok"):
        return [{"stage": "준비", "level": "bad", "msg": f"양식 데이터를 못 읽었습니다: {meta.get('error')}"}]
    cfg = config.load()
    c = client.JiraClient(cfg)
    try:
        create = next((s for s in steps if s.get("step") == 1), None)
        if not create:
            return [{"stage": "준비", "level": "bad", "msg": "1단계(등록) 요청이 없습니다"}]
        fields = (create.get("body") or {}).get("fields") or {}
        type_id = str((fields.get("issuetype") or {}).get("id") or "")
        project = (fields.get("project") or {}).get("key") or meta["project"]

        # ── 1. 등록 화면 ────────────────────────────────────────────────
        cm = c.get(f"/issue/createmeta/{project}/issuetypes/{type_id}", params={"maxResults": 200})
        screen = {f["fieldId"]: f for f in (cm.get("fields") or cm.get("values") or [])}
        names = {k: v.get("name", k) for k, v in screen.items()}
        unknown = [k for k in fields if k not in screen]
        if unknown:
            add("1. 등록", "bad", f"등록 화면에 없는 필드: {', '.join(unknown)} — Jira 가 거부한다")
        missing = [names[k] for k, f in screen.items()
                   if f.get("required") and not f.get("hasDefaultValue") and k not in ("project", "issuetype")
                   and fields.get(k) in (None, "", [], {})]
        add("1. 등록", "bad" if missing else "ok",
            f"필수 필드 비어 있음: {', '.join(missing)}" if missing else f"필수 필드 {sum(1 for f in screen.values() if f.get('required'))}개 모두 채움")
        bad_opts = []
        for k, v in fields.items():
            allowed = [a.get("value") or a.get("name") for a in (screen.get(k, {}).get("allowedValues") or [])]
            if not allowed or k in ("issuetype", "project"):
                continue
            vals = [x.get("value") or x.get("name") for x in (v if isinstance(v, list) else [v]) if isinstance(x, dict)]
            bad_opts += [f"{names.get(k, k)}='{x}'" for x in vals if x not in allowed]
        add("1. 등록", "bad" if bad_opts else "ok",
            f"허용되지 않는 선택값: {', '.join(bad_opts)}" if bad_opts else "선택값이 모두 허용 목록 안에 있음")

        # 사람: 배정 가능한지
        people = []
        if (fields.get("assignee") or {}).get("accountId"):
            people.append(("담당자", fields["assignee"]["accountId"]))
        for fk, label in ((F["engineer"], "엔지니어"), (F["cc"], "참조")):
            people += [(label, u.get("accountId")) for u in fields.get(fk) or [] if u.get("accountId")]
        name = {p["id"]: p["name"] for p in meta["people"]}
        for label, aid in people:
            try:
                found = c.get("/user/assignable/search", params={"project": project, "accountId": aid})
                ok = any(u.get("accountId") == aid and u.get("active", True) for u in (found or []))
            except client.JiraError:
                ok = False
            add("1. 등록", "ok" if ok else "bad",
                f"{label} {name.get(aid, aid)}: " + ("이 프로젝트에 배정 가능" if ok else "배정 불가 또는 비활성 계정"))

        # 상위
        pk = (fields.get("parent") or {}).get("key")
        if pk:
            try:
                pf = c.get(f"/issue/{pk}", params={"fields": "issuetype,status,project"})["fields"]
                if pf["issuetype"].get("subtask"):
                    add("1. 등록", "bad", f"상위 {pk} 가 하위 유형이라 그 아래에 만들 수 없음")
                elif pf["project"]["key"] != project:
                    add("1. 등록", "bad", f"상위 {pk} 가 다른 프로젝트")
                else:
                    add("1. 등록", "ok", f"상위 {pk} ({pf['issuetype']['name']}, {pf['status']['name']}) 확인")
            except client.JiraError:
                add("1. 등록", "bad", f"상위 {pk} 를 찾을 수 없음")

        # ── 2·3. 전환 ───────────────────────────────────────────────────
        for s in steps:
            tid = ((s.get("body") or {}).get("transition") or {}).get("id")
            if not tid:
                continue
            cat = "new" if tid == TRANSITIONS["start"] else "indeterminate"
            key = _sample(c, project, type_id, cat)
            if not key:
                add(f"{s['step']}. 전환", "warn", f"같은 유형의 표본 이슈가 없어 전환 {tid} 를 확인하지 못함")
                continue
            tr = c.get(f"/issue/{key}/transitions", params={"expand": "transitions.fields"}).get("transitions", [])
            hit = next((t for t in tr if t["id"] == tid), None)
            if not hit:
                add(f"{s['step']}. 전환", "bad", f"전환 id {tid} 가 없음 (표본 {key} 기준) — 워크플로가 바뀌었을 수 있음")
                continue
            req = [v.get("name") for v in (hit.get("fields") or {}).values() if v.get("required")]
            add(f"{s['step']}. 전환", "bad" if req else "ok",
                f"'{hit['name']}' → {hit['to']['name']}" + (f" — 전환 때 필수 입력: {', '.join(req)}" if req else " (묻는 필드 없음, 표본 " + key + ")"))

        # ── 4. 값 다시 넣기 (수정 화면) ─────────────────────────────────
        put = next((s for s in steps if s.get("method") == "PUT"), None)
        if put:
            final = "done" if any(((s.get("body") or {}).get("transition") or {}).get("id") == TRANSITIONS["close"]
                                  for s in steps) else "indeterminate"
            key = _sample(c, project, type_id, final)
            if not key:
                add(f"{put['step']}. 수정", "warn", "같은 상태의 표본 이슈가 없어 수정 가능 여부를 확인하지 못함")
            else:
                em = c.get(f"/issue/{key}/editmeta").get("fields", {})
                want = list(((put.get("body") or {}).get("fields") or {}).keys())
                miss = [names.get(k, k) for k in want if k not in em]
                add(f"{put['step']}. 수정", "bad" if miss else "ok",
                    f"{'종료' if final == 'done' else '진행 중'} 상태에서 수정 못 하는 필드: {', '.join(miss)} (표본 {key})"
                    if miss else f"{'종료' if final == 'done' else '진행 중'} 상태에서도 {len(want)}개 필드 수정 가능 (표본 {key})")
                for k, v in ((put.get("body") or {}).get("fields") or {}).items():
                    allowed = [a.get("value") for a in (em.get(k, {}).get("allowedValues") or [])]
                    if allowed and isinstance(v, dict) and v.get("value") not in allowed:
                        add(f"{put['step']}. 수정", "bad", f"{names.get(k, k)}='{v.get('value')}' 허용 안 됨")

        # ── 리포트 미리 판정 ─────────────────────────────────────────────
        merged = dict(fields)
        if put:
            merged.update((put.get("body") or {}).get("fields") or {})
        closed = any(((s.get("body") or {}).get("transition") or {}).get("id") == TRANSITIONS["close"] for s in steps)
        moved = any(((s.get("body") or {}).get("transition") or {}).get("id") for s in steps)
        tname = next((n for n, i in meta["types"].items() if i == type_id), "")
        desc = merged.get("description") or {}
        text = " ".join(t.get("text", "") for p in desc.get("content", []) for t in p.get("content", []) or [])
        issue = collect.Issue(
            key="(새 이슈)", summary=merged.get("summary") or "", kind=tname,
            status="종료" if closed else ("진행 중" if moved else "미해결"),
            assignee=name.get((merged.get("assignee") or {}).get("accountId")) if merged.get("assignee") else None,
            duedate=collect._date(merged.get("duedate")), actual_start=merged.get(F["actual_start"]),
            actual_end=merged.get(F["actual_end"]) if closed else None,
            compliance=(merged.get(F["compliance"]) or {}).get("value") or "Not Evaluated",
            delay_reason=(merged.get(F["delay"]) or {}).get("value"),
            reporter=meta["me"]["name"], engineer=", ".join(name.get(u.get("accountId"), "?") for u in merged.get(F["engineer"]) or []) or None,
            cc=", ".join(name.get(u.get("accountId"), "?") for u in merged.get(F["cc"]) or []) or None,
            start_date=merged.get(F["start_date"]), support=(merged.get(F["support"]) or {}).get("value"),
            category=(merged.get(F["category"]) or {}).get("value"),
            solution=", ".join(x.get("value", "") for x in merged.get(F["solution"]) or []) or None,
            parent=pk, has_description=bool(text.strip()),
        )
        project_parents = {p["key"] for p in meta["parents"] if p["kind"] == collect.PROJECT_KIND}
        skipped: dict[str, int] = {}
        found = collect._judge([issue], dt_today(), 3, project_parents, skipped)
        if found:
            for f in found:
                add("리포트 미리 판정", "warn", f"내일 리포트에 {'위험으로' if f.category == collect.RISK else '불일치로'} 잡힘: {f.reason}")
        else:
            note = " (진행 중 Not Evaluated 는 제외 규칙 대상)" if skipped else ""
            add("리포트 미리 판정", "ok", f"등록 후 상태 '{issue.status}' 기준, 매일 리포트에 걸리지 않음{note}")
        return out
    except client.JiraError as exc:
        return out + [{"stage": "Jira", "level": "bad", "msg": f"Jira 조회 실패: {str(exc)[:200]}"}]
    finally:
        c.close()


def dt_today():
    import datetime as _dt
    return _dt.date.today()
