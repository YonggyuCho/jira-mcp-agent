"""현황 페이지의 '상태 바꾸기' — 지금 가능한 전환 보기(읽기 토큰)와 바꾸기(쓰기 토큰).

허용 범위는 댓글과 같다 (jira_comment._in_scope). 전환은 **그 이슈에서 지금 가능한 것** 중에서만,
전환 때 Jira 가 따로 묻는 필수 칸이 있으면 웹에서 하지 않는다 (Jira 에서 직접).

Jira 자동화가 전환 때 시각을 덮는다 (등록 때와 같음, docs/WEB.md). 취소·중단 같은 다른 전환은 값을 묻지 않는다:
  - 진행 중으로 가는 전환('작업 시작') → Actual start 를 지금으로 덮음
  - 종료로 가는 전환('종결')          → Actual end 를 지금으로 덮음
그래서 전환한 뒤 화면에서 받은 값으로 다시 넣는다. 종료면 일정 준수(+ Delayed 면 지연 사유)도 같이 넣는다 —
비워 두면 다음 날 리포트에 'Not Evaluated' 위험으로 잡힌다.

쓰기는 .env WEB_STATUS_ENABLED=1 + JIRA_API_TOKEN_WRITE 일 때만. 기록은 runs/status.jsonl.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time

from jira_mcp import client, config

from . import jira_comment, jira_form

F = jira_form.F
DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")  # 화면의 datetime-local 값


def enabled() -> bool:
    return (os.environ.get("WEB_STATUS_ENABLED", "").strip() == "1"
            and bool(os.environ.get("JIRA_API_TOKEN_WRITE", "").strip()))


def _jira_dt(v: str) -> str:
    return f"{v}:00.000+0900"


def _local(v: str | None) -> str:
    """Jira 시각 → 화면 datetime-local (KST). 없으면 ''."""
    if not v:
        return ""
    try:  # Jira 는 +0900 형식 (콜론 없음) — 3.10 의 fromisoformat 은 못 읽는다
        t = dt.datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%f%z")
        return t.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


def _transitions(c, key: str) -> list[dict]:
    tr = c.get(f"/issue/{key}/transitions", params={"expand": "transitions.fields"}).get("transitions", [])
    return [{"id": t["id"], "name": t["name"], "to": t["to"]["name"],
             "cat": (t["to"].get("statusCategory") or {}).get("key", ""),
             # 시각을 덮는 건 '작업 시작'·'종결' 두 전환뿐이다. 취소(완료 범주)·중단(Pending)은 값을 안 묻는다
             "need": {jira_form.TRANSITIONS["start"]: "start", jira_form.TRANSITIONS["close"]: "close"}.get(t["id"], ""),
             "required": [v.get("name") for v in (t.get("fields") or {}).values() if v.get("required")]}
            for t in tr]


def options(key: str) -> dict:
    """지금 가능한 전환 + 다시 넣을 칸의 현재 값."""
    c = client.JiraClient(config.load())
    try:
        ok, why, title = jira_comment._in_scope(c, key)
        if not ok:
            return {"ok": False, "error": why}
        f = c.get(f"/issue/{key}", params={"fields": f"status,duedate,{F['actual_start']},{F['actual_end']},"
                                                     f"{F['compliance']},{F['delay']}"})["fields"]
        opts = jira_form.form_meta().get("options", {})
        return {"ok": True, "key": key, "title": title, "status": f["status"]["name"],
                "due": f.get("duedate") or "", "enabled": enabled(),
                "actual_start": _local(f.get(F["actual_start"])), "actual_end": _local(f.get(F["actual_end"])),
                "compliance": (f.get(F["compliance"]) or {}).get("value") or "",
                "delay": (f.get(F["delay"]) or {}).get("value") or "",
                "compliance_options": opts.get("compliance", []), "delay_options": opts.get("delay", []),
                "transitions": _transitions(c, key)}
    except client.JiraError as exc:
        return {"ok": False, "error": f"Jira 조회 실패: {str(exc)[:200]}"}
    finally:
        c.close()


def _log(entry: dict) -> None:
    from agent import store
    store.RUNS.mkdir(parents=True, exist_ok=True)
    with open(store.RUNS / "status.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def change(key: str, tid: str, values: dict) -> dict:
    """values = {actual_start, actual_end, compliance, delay} — 전환 도착 상태에 따라 필요한 것만 쓴다."""
    result: dict = {"ok": False, "key": key, "error": None, "done": []}
    values = values or {}
    if not enabled():
        result["error"] = "상태 바꾸기가 꺼져 있습니다 (.env WEB_STATUS_ENABLED)"
        return result
    rc = client.JiraClient(config.load())
    try:
        ok, why, title = jira_comment._in_scope(rc, key)
        tr = next((t for t in _transitions(rc, key) if t["id"] == str(tid)), None) if ok else None
        before = rc.get(f"/issue/{key}", params={"fields": "status"})["fields"]["status"]["name"] if ok else ""
    except client.JiraError as exc:
        ok, why, tr = False, f"Jira 조회 실패: {str(exc)[:200]}", None
    finally:
        rc.close()
    if not ok:
        result["error"] = why
        return result
    if not tr:
        result["error"] = "지금 이 이슈에서 할 수 없는 전환입니다 — 화면을 새로 고쳐 다시 고르세요"
        return result
    if tr["required"]:
        result["error"] = f"전환 때 Jira 가 따로 묻는 칸이 있어 웹에서는 못 합니다: {', '.join(tr['required'])} — Jira 에서 직접"
        return result

    # 전환 뒤 다시 넣을 값 — 시각을 덮는 두 전환(작업 시작·종결)일 때만
    put: dict = {}
    if tr["need"] == "start":
        if not DT.match(values.get("actual_start") or ""):
            result["error"] = "Actual start 를 넣어 주세요"
            return result
        put[F["actual_start"]] = _jira_dt(values["actual_start"])
    elif tr["need"] == "close":
        if not DT.match(values.get("actual_end") or ""):
            result["error"] = "Actual end 를 넣어 주세요"
            return result
        allowed = jira_form.form_meta().get("options", {})
        comp = values.get("compliance") or ""
        if comp not in allowed.get("compliance", []):
            result["error"] = "일정 준수 여부를 골라 주세요"
            return result
        put[F["actual_end"]] = _jira_dt(values["actual_end"])
        put[F["compliance"]] = {"value": comp}
        if comp == "Delayed":
            if (values.get("delay") or "") not in allowed.get("delay", []):
                result["error"] = "Delayed 면 지연 사유를 골라 주세요"
                return result
            put[F["delay"]] = {"value": values["delay"]}
        # 진행 중을 거치지 않고 바로 종료하는 경우도 Actual start 를 지켜 준다
        if DT.match(values.get("actual_start") or ""):
            put[F["actual_start"]] = _jira_dt(values["actual_start"])

    w = jira_form._write_client()
    try:
        w.post(f"/issue/{key}/transitions", json={"transition": {"id": tr["id"]}})
        result["done"].append(f"'{tr['name']}' → {tr['to']}")
        if put:
            w.request("PUT", f"/issue/{key}", json={"fields": put})  # 공용 클라이언트에 put 을 안 늘린다
            names = {F["actual_start"]: "Actual start", F["actual_end"]: "Actual end",
                     F["compliance"]: "일정 준수", F["delay"]: "지연 사유"}
            result["done"].append("다시 넣음: " + ", ".join(names[k] for k in put))
        result.update(ok=True, status=tr["to"],
                      url=f"{config.load().site.rstrip('/')}/browse/{key}")
    except client.JiraError as exc:
        step = "전환" if not result["done"] else "값 다시 넣기 (상태는 이미 바뀜)"
        result["error"] = f"{step}에서 Jira 가 거절했습니다: {str(exc)[:300]}"
    finally:
        w.close()
    _log({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "key": key, "title": title, "from": before,
          "to": tr["to"], "transition": tr["name"], "ok": result["ok"], "error": result["error"],
          "done": result["done"], "values": {k: v for k, v in values.items() if v}})
    return result
