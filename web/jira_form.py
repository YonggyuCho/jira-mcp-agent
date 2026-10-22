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
from concurrent.futures import ThreadPoolExecutor

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
        # 유형별로 등록 화면에 보고자 칸이 있는지 (기술지원은 없다 — 만든 사람이 보고자가 된다, 2026-10-12 실측)
        type_ids = {}
        for name in TYPES:
            cands = [t for t in types if t["name"] == name]
            pick = next((t for t in cands if t.get("subtask")), cands[0] if cands else None)
            if pick:
                type_ids[name] = pick["id"]

        # 서로 의존하지 않는 호출은 동시에 보낸다 (Jira Cloud 왕복이 호출당 0.5~1초라 순서대로면 느리다)
        with ThreadPoolExecutor(max_workers=6) as ex:
            metas_f = {name: ex.submit(c.get, f"/issue/createmeta/{project}/issuetypes/{tid}",
                                       params={"maxResults": 200}) for name, tid in type_ids.items()}
            me_f = ex.submit(c.get, "/myself")
            epic_f = ex.submit(c.get, f"/issue/{epic}", params={"fields": "summary"}) if epic else None
            metas = {name: f.result() for name, f in metas_f.items()}
            me = me_f.result()
            epic_title = (epic_f.result()["fields"].get("summary") or "").strip() if epic_f else ""

        reporter_ok = {name: any(f["fieldId"] == "reporter" for f in (m.get("fields") or m.get("values") or []))
                       for name, m in metas.items()}

        # 선택지 (요청 경로·권역) — 기술지원 유형의 메타에서
        options: dict[str, list[str]] = {}
        if "기술지원" in metas:
            meta = metas["기술지원"]
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
        # 보고자 고정 (.env WEB_FORM_REPORTER, 표시 이름). 사람 목록에서 계정 ID 를 찾는다.
        rname = os.environ.get("WEB_FORM_REPORTER", "").strip()
        rid = next((u.get("accountId") for r in rows for u in [r["fields"].get("reporter"), r["fields"].get("assignee")]
                    if isinstance(u, dict) and u.get("displayName") == rname), None) if rname else None
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
            "reporter": {"id": rid, "name": rname if rid else None, "on_screen": reporter_ok},
            "fixed": fixed, "options": {"route": options.get(F["route"], []), "region": options.get(F["region"], []),
                                         "delay": options.get(F["delay"], []), "compliance": options.get(F["compliance"], []),
                                         "category": options.get(F["category"], [])},
            "transitions": TRANSITIONS,
            "create_enabled": create_enabled(),
            "category": {"project_child": collect.PROJECT_CHILD_CATEGORY, "default": collect.EXPECT_CATEGORY,
                         "meeting_kind": collect.MEETING_KIND, "project_kind": collect.PROJECT_KIND},
            "fields": F, "basis": len(rows),
        }
    finally:
        c.close()


def _rebuild() -> dict:
    try:
        data = _build()
    except Exception as exc:  # 화면은 떠야 한다 — 오류를 화면에 보여 준다
        return {"ok": False, "error": str(exc)[:300]}
    _cache.update(at=time.time(), data=data)
    return data


def _refresh_bg() -> None:
    if _lock.acquire(blocking=False):  # 이미 누가 다시 읽는 중이면 그대로 둔다
        def run():
            try:
                _rebuild()
            finally:
                _lock.release()
        threading.Thread(target=run, daemon=True).start()


def warm() -> None:
    """서버 기동 직후 미리 읽어 둔다 — 첫 접속이 기다리지 않게."""
    _refresh_bg()


def form_meta(refresh: bool = False) -> dict:
    """캐시를 바로 돌려준다. 10분이 지났으면 돌려준 뒤 뒤에서 다시 읽는다 (화면은 기다리지 않는다).
    캐시가 아예 없거나 refresh=True('Jira 에서 다시 읽기')일 때만 기다린다. 못 읽으면 ok=False 와 오류 문구."""
    data = _cache["data"]
    if data and not refresh:
        if time.time() - _cache["at"] >= CACHE_SEC:
            _refresh_bg()
        return data
    with _lock:
        if not refresh and _cache["data"]:  # 기다리는 동안 다른 요청이 채웠다
            return _cache["data"]
        return _rebuild()


# ── 검증 (dry-run) ─────────────────────────────────────────────────────────
# Jira 에는 '등록 미리 시험' API 가 없다. 그래서 읽기 API 로 실제 등록 때 실패할 지점을 하나씩 확인한다.
# 아무것도 쓰지 않는다.

def _sample(c, project: str, type_id: str, cat: str, status: str | None = None) -> str | None:
    """같은 유형에서 상태 범주가 cat(new / indeterminate / done)인 이슈 하나.
    status(상태 id)를 주면 그 상태인 이슈만 — 같은 '진행' 범주라도 Pending 은 종료 전환이 없다 (2026-10-22 오탐)."""
    names = {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}
    cond = f"status = {status}" if status else f'statusCategory = "{names[cat]}"'
    r = c.post("/search/jql", json={"jql": f'project = {project} AND issuetype = {type_id} '
                                            f'AND {cond} ORDER BY updated DESC',
                                     "maxResults": 1, "fields": ["status"]})
    iss = r.get("issues") or []
    return iss[0]["key"] if iss else None


def _stage(s: dict) -> str:
    """화면에 보일 단계 이름."""
    tid = ((s.get("body") or {}).get("transition") or {}).get("id")
    if tid == TRANSITIONS["start"]:
        what = "진행 중으로 바꾸기"
    elif tid == TRANSITIONS["close"]:
        what = "종료로 바꾸기"
    elif s.get("method") == "PUT":
        what = "실제 시작·종료 시각 다시 넣기"
    else:
        what = "이슈 만들기"
    return f"{s.get('step')}. {what}"


def _same_flow_sample(c, project: str, type_id: str, cat: str, status: str | None = None) -> tuple[str | None, str]:
    """같은 유형에 표본이 없을 때, 상태 목록이 완전히 같은 다른 등록 유형에서 표본을 찾는다.
    회의는 대부분 바로 종료돼 진행 전·진행 중 표본이 거의 없다 (2026-10-12 실측: 0건)."""
    flows = {t["id"]: (t["name"], sorted(x["name"] for x in t["statuses"]))
             for t in c.get(f"/project/{project}/statuses")}
    mine = flows.get(type_id, (None, None))[1]
    names = {v: k for k, v in form_meta()["types"].items()}
    for tid, (_, sts) in flows.items():
        if tid != type_id and tid in names and sts == mine:
            key = _sample(c, project, tid, cat, status)
            if key:
                return key, names[tid]
    return None, ""


def validate(steps: list[dict], images: list[dict] | None = None) -> list[dict]:
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
        s1 = _stage(create)
        type_id = str((fields.get("issuetype") or {}).get("id") or "")
        project = (fields.get("project") or {}).get("key") or meta["project"]

        # ── 1. 등록 화면 ────────────────────────────────────────────────
        cm = c.get(f"/issue/createmeta/{project}/issuetypes/{type_id}", params={"maxResults": 200})
        screen = {f["fieldId"]: f for f in (cm.get("fields") or cm.get("values") or [])}
        names = {k: v.get("name", k) for k, v in screen.items()}
        unknown = [k for k in fields if k not in screen]
        if unknown:
            add(s1, "bad", f"Jira 등록 화면에 없는 칸이 들어 있음: {', '.join(unknown)}")
        missing = [names[k] for k, f in screen.items()
                   if f.get("required") and not f.get("hasDefaultValue") and k not in ("project", "issuetype")
                   and fields.get(k) in (None, "", [], {})]
        add(s1, "bad" if missing else "ok",
            f"필수 칸이 비어 있음: {', '.join(missing)}" if missing
            else f"필수 칸 {sum(1 for f in screen.values() if f.get('required'))}개 모두 채움")
        bad_opts = []
        for k, v in fields.items():
            allowed = [a.get("value") or a.get("name") for a in (screen.get(k, {}).get("allowedValues") or [])]
            if not allowed or k in ("issuetype", "project"):
                continue
            vals = [x.get("value") or x.get("name") for x in (v if isinstance(v, list) else [v]) if isinstance(x, dict)]
            bad_opts += [f"{names.get(k, k)}='{x}'" for x in vals if x not in allowed]
        add(s1, "bad" if bad_opts else "ok",
            f"Jira 가 받지 않는 선택값: {', '.join(bad_opts)}" if bad_opts else "선택값 모두 Jira 허용 목록 안")

        # 사람: 배정 가능한지
        people = []
        if (fields.get("assignee") or {}).get("accountId"):
            people.append(("담당자", fields["assignee"]["accountId"]))
        for fk, label in ((F["engineer"], "엔지니어"), (F["cc"], "참조")):
            people += [(label, u.get("accountId")) for u in fields.get(fk) or [] if u.get("accountId")]
        name = {p["id"]: p["name"] for p in meta["people"]}
        ok_people = []
        for label, aid in people:
            try:
                found = c.get("/user/assignable/search", params={"project": project, "accountId": aid})
                ok = any(u.get("accountId") == aid and u.get("active", True) for u in (found or []))
            except client.JiraError:
                ok = False
            if ok:
                ok_people.append(f"{label} {name.get(aid, aid)}")
            else:
                add(s1, "bad", f"{label} {name.get(aid, aid)}: 이 프로젝트에 배정할 수 없거나 비활성 계정")
        if ok_people:
            add(s1, "ok", f"사람 {len(ok_people)}명 배정 가능 — {', '.join(ok_people)}")

        # 상위
        pk = (fields.get("parent") or {}).get("key")
        if pk:
            try:
                pf = c.get(f"/issue/{pk}", params={"fields": "issuetype,status,project"})["fields"]
                if pf["issuetype"].get("subtask"):
                    add(s1, "bad", f"상위 {pk} 가 하위 유형이라 그 아래에 만들 수 없음")
                elif pf["project"]["key"] != project:
                    add(s1, "bad", f"상위 {pk} 가 다른 프로젝트")
                else:
                    add(s1, "ok", f"상위 {pk} 있음 ({pf['issuetype']['name']}, {pf['status']['name']})")
            except client.JiraError:
                add(s1, "bad", f"상위 {pk} 를 찾을 수 없음")

        # ── 붙여넣은 사진 (설명) ──────────────────────────────────────────
        if images:
            from . import jira_media
            why = jira_media.check_meta(images)
            am = c.get("/attachment/meta")
            if not am.get("enabled"):
                why = why or "이 Jira 는 첨부가 꺼져 있습니다"
            mb = sum(int(i.get("size") or 0) for i in images) / 1048576
            add(s1, "bad" if why else "ok",
                why or f"사진 {len(images)}장 ({mb:.1f}MB) — 이슈를 만든 직후 첨부로 올리고 설명의 [사진 n] 자리에 넣음")

        # ── 2·3. 전환 ───────────────────────────────────────────────────
        after = {}  # 전환 id → 도착 상태 id. 다음 단계는 정확히 그 상태의 표본으로 본다 (같은 범주라도 Pending·취소는 다름)
        for s in steps:
            tid = ((s.get("body") or {}).get("transition") or {}).get("id")
            if not tid:
                continue
            # Jira 에는 전환을 미리 시험하는 API 가 없다. 같은 상태에 있는 다른 이슈에서
            # '지금 할 수 있는 전환' 을 조회해 이 전환이 있는지, 전환 때 따로 묻는 칸이 있는지 본다.
            cat = "new" if tid == TRANSITIONS["start"] else "indeterminate"
            state = "진행 전" if cat == "new" else "진행 중"
            st = after.get(TRANSITIONS["start"]) if cat == "indeterminate" else None
            key, via = _sample(c, project, type_id, cat, st), ""
            if not key:
                key, other = _same_flow_sample(c, project, type_id, cat, st)
                via = f" · {state}인 같은 유형이 없어, 상태 흐름이 같은 '{other}' {key} 로 확인" if key else ""
            if not key:
                add(_stage(s), "warn", f"미리 확인 못 함 — {state}인 이슈가 없음. "
                                       "실패하면 이슈는 만들어진 채 멈추니 Jira 에서 직접 바꾸면 됨")
                continue
            tr = c.get(f"/issue/{key}/transitions", params={"expand": "transitions.fields"}).get("transitions", [])
            hit = next((t for t in tr if t["id"] == tid), None)
            if not hit:
                add(_stage(s), "bad", f"전환(id {tid})이 없음 — Jira 워크플로가 바뀌었을 수 있음 (표본 {key})")
                continue
            after[tid] = hit["to"]["id"]
            req = [v.get("name") for v in (hit.get("fields") or {}).values() if v.get("required")]
            add(_stage(s), "bad" if req else "ok",
                f"'{hit['name']}' 전환 가능 → {hit['to']['name']}"
                + (f" — 전환 때 필수 입력: {', '.join(req)}" if req else (via or f" (표본 {key})")))

        # ── 4. 값 다시 넣기 (수정 화면) ─────────────────────────────────
        put = next((s for s in steps if s.get("method") == "PUT"), None)
        if put:
            final = "done" if any(((s.get("body") or {}).get("transition") or {}).get("id") == TRANSITIONS["close"]
                                  for s in steps) else "indeterminate"
            key = _sample(c, project, type_id, final, after.get(TRANSITIONS["close"] if final == "done" else TRANSITIONS["start"]))
            if not key:
                add(_stage(put), "warn", "미리 확인 못 함 — 같은 상태의 이슈가 없음")
            else:
                em = c.get(f"/issue/{key}/editmeta").get("fields", {})
                want = list(((put.get("body") or {}).get("fields") or {}).keys())
                miss = [names.get(k, k) for k in want if k not in em]
                state = '종료' if final == 'done' else '진행 중'
                add(_stage(put), "bad" if miss else "ok",
                    f"{state} 상태에서 고칠 수 없는 칸: {', '.join(miss)} (표본 {key})" if miss
                    else f"{state} 상태에서도 고칠 수 있음 — {', '.join(names.get(k, k) for k in want)} (표본 {key})")
                for k, v in ((put.get("body") or {}).get("fields") or {}).items():
                    allowed = [a.get("value") for a in (em.get(k, {}).get("allowedValues") or [])]
                    if allowed and isinstance(v, dict) and v.get("value") not in allowed:
                        add(_stage(put), "bad", f"{names.get(k, k)}='{v.get('value')}' 는 Jira 가 받지 않음")

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
                add("매일 점검 미리보기", "warn", f"등록하면 내일 리포트에 {'위험' if f.category == collect.RISK else '불일치'}으로 잡힘 — {f.reason}")
        else:
            note = " (진행 중 Not Evaluated 는 제외 규칙 대상)" if skipped else ""
            add("매일 점검 미리보기", "ok", f"등록 뒤 '{issue.status}' 상태 기준으로 위험·불일치 없음{note}")
        return out
    except client.JiraError as exc:
        return out + [{"stage": "Jira", "level": "bad", "msg": f"Jira 조회 실패: {str(exc)[:200]}"}]
    finally:
        c.close()


def dt_today():
    import datetime as _dt
    return _dt.date.today()


# ── 실제 등록 ──────────────────────────────────────────────────────────────
# 화면이 보낸 단계를 그대로 믿지 않는다. 서버가 허용 범위를 다시 확인하고, 직전에 Dry-run 을 한 번 더 돌린다.
# 쓰기 토큰(JIRA_API_TOKEN_WRITE)은 여기서만 쓴다. .env 의 WEB_CREATE_ENABLED=1 일 때만 동작한다.

PUT_ALLOWED = {F["actual_start"], F["actual_end"], F["compliance"], F["delay"]}


def create_enabled() -> bool:
    return os.environ.get("WEB_CREATE_ENABLED", "").strip() == "1" and bool(os.environ.get("JIRA_API_TOKEN_WRITE", "").strip())


def _write_client():
    import dataclasses
    return client.JiraClient(dataclasses.replace(config.load(), token=os.environ["JIRA_API_TOKEN_WRITE"].strip()))


def _check_scope(steps: list[dict], meta: dict) -> str | None:
    """허용 범위 밖이면 이유를 돌려준다."""
    if not steps or steps[0].get("step") != 1 or steps[0].get("method") != "POST":
        return "1단계(등록)가 없습니다"
    f = (steps[0].get("body") or {}).get("fields") or {}
    if (f.get("project") or {}).get("key") != meta["project"]:
        return "다른 프로젝트에는 등록할 수 없습니다"
    if str((f.get("issuetype") or {}).get("id")) not in set(meta["types"].values()):
        return "허용되지 않은 유형입니다"
    if (f.get("parent") or {}).get("key") not in {p["key"] for p in meta["parents"]}:
        return "허용되지 않은 상위입니다 (에픽 직속 프로젝트·구축만)"
    for s in steps[1:]:
        tid = ((s.get("body") or {}).get("transition") or {}).get("id")
        if s.get("method") == "POST" and tid not in TRANSITIONS.values():
            return f"허용되지 않은 전환입니다: {tid}"
        if s.get("method") == "PUT" and set(((s.get("body") or {}).get("fields") or {})) - PUT_ALLOWED:
            return "마지막 단계에서 바꿀 수 없는 필드가 있습니다"
        if s.get("method") not in ("POST", "PUT"):
            return "알 수 없는 단계입니다"
    return None


def _log(entry: dict) -> None:
    """등록 기록 — runs/creates.jsonl (이슈 키·제목이 있어 공개 저장소에 안 올라간다)."""
    import json
    from agent import store
    store.RUNS.mkdir(parents=True, exist_ok=True)
    with open(store.RUNS / "creates.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def create(steps: list[dict], desc: str = "", images: list[dict] | None = None) -> dict:
    """결과: {ok, key, url, done:[단계 설명], error, dry:[검증 결과]}"""
    import datetime as _dt
    started = _dt.datetime.now().isoformat(timespec="seconds")
    result: dict = {"ok": False, "key": None, "url": None, "done": [], "error": None, "at": started}
    if not create_enabled():
        result["error"] = "실제 등록이 꺼져 있습니다 (WEB_CREATE_ENABLED)"
        return result
    meta = form_meta()
    if not meta.get("ok"):
        result["error"] = f"양식 데이터를 못 읽었습니다: {meta.get('error')}"
        return result
    why = _check_scope(steps, meta)
    if why:
        result["error"] = why
        return result
    from . import jira_media
    imgs, why = jira_media.decode(images or [])
    if why:
        result["error"] = why
        return result
    dry = validate(steps, [{"n": i["n"], "size": len(i["bytes"]), "type": i["mime"]} for i in imgs])
    result["dry"] = dry
    if any(x["level"] == "bad" for x in dry):
        result["error"] = "Dry-run 실패 — 보내지 않았습니다"
        return result

    c = _write_client()
    site = config.load().site
    try:
        body = steps[0]["body"]
        if desc.strip():
            # 설명은 서버가 만든다 — 주소를 링크로. 사진 자리 표시는 사진을 올린 뒤에 채운다.
            body = {**body, "fields": {**body["fields"], "description": jira_media.to_adf(jira_media.strip_tokens(desc) if imgs else desc)}}
        created = c.post("/issue", json=body)
        key = created.get("key")
        result.update(key=key, url=f"{site}/browse/{key}")
        result["done"].append(f"1. 등록 → {key}")
        if imgs:
            media = jira_media.upload_all(c, key, imgs, result["done"])
            c.request("PUT", f"/issue/{key}", json={"fields": {"description": jira_media.to_adf(desc, media)}})
            result["done"].append(f"1. 설명에 사진 {len(imgs)}장 넣기")
        for s in steps[1:]:
            path = s["path"].replace("{키}", key)
            if s["method"] == "POST":
                c.post(path.replace("/rest/api/3", ""), json=s["body"])
            else:
                c.request("PUT", path.replace("/rest/api/3", ""), json=s["body"])  # 공용 클라이언트에 put 을 안 늘린다 (MCP 는 읽기 전용)
            result["done"].append(f"{s['step']}. {s.get('what', '')}")
        result["ok"] = True
    except client.JiraError as exc:
        result["error"] = f"{len(result['done']) + 1}단계에서 실패: {str(exc)[:300]}"
    finally:
        c.close()
        title = ((steps[0].get("body") or {}).get("fields") or {}).get("summary")
        _log({**{k: v for k, v in result.items() if k != "dry"}, "title": title,
              "steps": [s.get("what") for s in steps]})
    return result
