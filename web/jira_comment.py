"""현황 페이지의 '댓글' — 이슈 댓글 보기(읽기 토큰)와 달기(쓰기 토큰).

허용 범위는 서버가 다시 확인한다: 프로젝트 = AGENT_PROJECT 이고, 솔루션에 AGENT_SOLUTION 이 들어 있거나
가장 최근 리포트 표에 나온 이슈만.
@ 알림(멘션)은 넣지 않는다 — 입력한 글은 평문 문단으로만 보낸다 (사용자 결정 10-15).
붙여넣은 사진은 그 이슈의 첨부로 올리고 글 속 자리에 보이게 넣는다 (web/jira_media.py).
쓰기는 .env 의 WEB_COMMENT_ENABLED=1 + JIRA_API_TOKEN_WRITE 일 때만. 기록은 runs/comments.jsonl.
"""
from __future__ import annotations

import json
import os
import re
import time

from jira_mcp import adf, client, config

from . import jira_form, jira_media

KEY = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
MAX_LEN = 5000
SOLUTION_FIELD = jira_form.F["solution"]


def enabled() -> bool:
    return (os.environ.get("WEB_COMMENT_ENABLED", "").strip() == "1"
            and bool(os.environ.get("JIRA_API_TOKEN_WRITE", "").strip()))


def _table_keys() -> set[str]:
    """가장 최근 리포트(현황 페이지 표)에 나온 이슈 키."""
    from agent import store
    latest = next((r for r in store.load_all() if r.get("report")), None)
    return {row["issue"]["key"] for row in (latest or {}).get("report", {}).get("rows", [])}


def _in_scope(c, key: str) -> tuple[bool, str, str]:
    """(허용, 이유, 제목). 화면이 보낸 키를 그대로 믿지 않는다."""
    if not KEY.match(key or ""):
        return False, "이슈 키 형식이 아닙니다", ""
    project = os.environ.get("AGENT_PROJECT", "").strip()
    solution = os.environ.get("AGENT_SOLUTION", "").strip()
    try:
        f = c.get(f"/issue/{key}", params={"fields": f"summary,project,{SOLUTION_FIELD}"})["fields"]
    except client.JiraError:
        return False, "이슈를 찾을 수 없습니다", ""
    sols = [x.get("value") for x in f.get(SOLUTION_FIELD) or [] if isinstance(x, dict)]
    # 현황 표에 나온 이슈도 허용 — 에픽 아래인데 솔루션이 틀린 이슈(불일치)에 "고쳐 달라" 고 다는 경우
    if f["project"]["key"] == project and key in _table_keys():
        return True, "", f.get("summary") or ""
    if f["project"]["key"] != project or solution not in sols:
        return False, f"{solution} 이슈에만 댓글을 달 수 있습니다", f.get("summary") or ""
    return True, "", f.get("summary") or ""


def list_comments(key: str, limit: int = 30) -> dict:
    """최근 댓글(최신순)."""
    c = client.JiraClient(config.load())
    try:
        ok, why, title = _in_scope(c, key)
        if not ok:
            return {"ok": False, "error": why}
        r = c.get(f"/issue/{key}/comment", params={"orderBy": "-created", "maxResults": limit})
        items = [{"author": (x.get("author") or {}).get("displayName") or "?",
                  "at": (x.get("created") or "")[:16].replace("T", " "),
                  "text": adf.flatten(x.get("body"), limit=3000)} for x in r.get("comments") or []]
        return {"ok": True, "key": key, "title": title, "total": r.get("total", len(items)),
                "comments": items, "enabled": enabled(), "author": jira_form.form_meta().get("me", {}).get("name")}
    except client.JiraError as exc:
        return {"ok": False, "error": f"Jira 조회 실패: {str(exc)[:200]}"}
    finally:
        c.close()


def _log(entry: dict) -> None:
    from agent import store
    store.RUNS.mkdir(parents=True, exist_ok=True)
    with open(store.RUNS / "comments.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def add_comment(key: str, text: str, images: list[dict] | None = None) -> dict:
    """images = [{n, data}] — 글 속 `[사진 n]` 자리에 들어간다 (web/jira_media.py)."""
    text = (text or "").replace("\r\n", "\n").strip()
    result = {"ok": False, "key": key, "error": None, "done": []}
    if not enabled():
        result["error"] = "댓글 달기가 꺼져 있습니다 (.env WEB_COMMENT_ENABLED)"
        return result
    imgs, why = jira_media.decode(images or [])
    if why:
        result["error"] = why
        return result
    if not text and not imgs:
        result["error"] = "댓글 내용이 비어 있습니다"
        return result
    if len(text) > MAX_LEN:
        result["error"] = f"{MAX_LEN}자 이하로 써 주세요 (지금 {len(text)}자)"
        return result
    rc = client.JiraClient(config.load())
    try:
        ok, why, title = _in_scope(rc, key)
    finally:
        rc.close()
    if not ok:
        result["error"] = why
        return result
    w = jira_form._write_client()
    try:
        media = jira_media.upload_all(w, key, imgs, result["done"]) if imgs else {}
        r = w.post(f"/issue/{key}/comment", json={"body": jira_media.to_adf(text, media)})
        result.update(ok=True, id=r.get("id"),
                      url=f"{config.load().site.rstrip('/')}/browse/{key}?focusedCommentId={r.get('id')}")
    except client.JiraError as exc:
        left = f" — 이미 올라간 첨부: {', '.join(result['done'])} (이슈 첨부 목록에 남아 있음)" if result["done"] else ""
        result["error"] = f"Jira 가 거절했습니다: {str(exc)[:300]}{left}"
    finally:
        w.close()
    _log({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "key": key, "title": title, "ok": result["ok"],
          "id": result.get("id"), "error": result["error"], "chars": len(text), "images": len(imgs), "preview": text[:200]})
    return result
