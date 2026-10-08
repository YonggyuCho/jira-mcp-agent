"""에이전트 웹 화면 (v2 — 2026-10-08 디자인 개편). 표준 라이브러리만 쓴다.

  python -m web            # 127.0.0.1:8090  (WEB_UI=v1 이면 예전 화면 web/server_v1.py)

화면 모양은 전부 web/templates/app.html 에 있다 (Claude 디자인 목업을 옮긴 것).
서버는 그 안의 `/*__DATA__*/null` 자리에 JSON 을 넣기만 한다 — 디자인을 바꿀 때 파이썬을 안 건드린다.

밖에서 바로 붙지 않는다. nginx 가 HTTPS 를 맡고 여기로 넘긴다. 그래서 127.0.0.1 에만 묶는다.
판정은 하지 않는다 — agent 가 남긴 runs/*.json 을 보여 주고, 버튼은 cron 과 똑같은
명령(python -m agent.daily)을 실행할 뿐이다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from agent import store

from . import jira_form

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "templates" / "app.html"
HOST = os.environ.get("WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEB_PORT", "8090"))
_running = threading.Lock()  # 발송·미리보기가 겹치지 않게

OUTCOME = {"sent": "발송", "dry-run": "미리보기", "collect-failed": "수집 실패", "send-failed": "발송 실패"}
TRIGGER = {"cron": "자동", "web": "웹", "manual": "수동"}
FAILED = ("collect-failed", "send-failed")


def _at(s: str) -> str:
    return (s or "").replace("T", " ")


def _report(run: dict, prev: dict | None) -> tuple[dict | None, dict]:
    """runs/*.json 한 건 → 템플릿이 쓰는 REPORT 모양 (+ 이전 대비)."""
    rep = run.get("report")
    if not rep:
        return None, {}
    d = store.diff(run, prev)
    rows = []
    for r in rep["rows"]:
        i = r["issue"]
        verdict = [v for v, on in (("위험", r["risk"]), ("불일치", r["mismatch"])) if on] or ["정상"]
        rows.append({
            "verdict": verdict, "parent": i.get("parent_title") or "-",
            # 계층 2단: Epic / 프로젝트·구축. 예전 기록(필드 없음)은 바로 위 제목을 프로젝트로 본다.
            "epic": i.get("epic_title") or "-", "project": i.get("project_title") or i.get("parent_title") or "-", "key": i["key"], "url": i["url"],
            "title": i["summary"], "kind": i["kind"], "status": i["status"], "assignee": i.get("assignee") or "",
            "due": i.get("duedate") or "-", "compliance": i.get("compliance") or "-",
            "risk": r["risk"], "mismatch": r["mismatch"], "new": i["key"] in d["new"],
        })
    counts = {"위험": rep["risk"], "불일치": rep["mismatch"], "정상": rep["ok"], "전체": rep["total"],
              "상태별": ", ".join(f"{k} {v}" for k, v in sorted(rep["counts"].items()))}
    report = {
        "title": run.get("title") or f"{rep['solution']} {rep['today']}",
        "at": _at(run["at"]), "trigger": TRIGGER.get(run["trigger"], run["trigger"]),
        "outcome": OUTCOME.get(run["outcome"], run["outcome"]), "webhook": run.get("webhook_status") or "",
        "counts": counts, "skipped": ", ".join(f"{k} {v}건" for k, v in rep.get("skipped", {}).items()),
        "rows": rows,
    }
    diff = {"base": _at(d["base"]) if d["base"] else None, "new": d["new"], "resolved": d["resolved"]}
    return report, diff


def _runs_list(runs: list[dict]) -> list[dict]:
    out = []
    for r in runs[:200]:
        rep = r.get("report") or {}
        out.append({"id": r["id"], "at": _at(r["at"]), "src": TRIGGER.get(r["trigger"], r["trigger"]),
                    "res": OUTCOME.get(r["outcome"], r["outcome"]),
                    "r": rep.get("risk") if rep else None, "m": rep.get("mismatch") if rep else None,
                    "hook": r.get("webhook_status") or "", "err": r.get("error") or ""})
    return out


def _prev_of(run: dict, runs: list[dict]) -> dict | None:
    """비교 기준: 이 실행보다 앞선, 결과가 있는 실행 중 '다른 날짜' 의 마지막 것 (=어제 리포트)."""
    day = run["at"][:10]
    for r in runs:
        if r["at"] < run["at"] and r.get("report") and r["at"][:10] != day:
            return r
    return None


def render(data: dict) -> bytes:
    # </script> 로 스크립트가 끊기지 않게 '</' 를 이스케이프한다.
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob, 1).encode()


def today_data(msg: str = "") -> dict:
    runs = store.load_all()
    latest = next((r for r in runs if r.get("report")), None)
    data: dict = {"page": "today", "runs": []}
    if latest:
        data["report"], data["diff"] = _report(latest, _prev_of(latest, runs))
        data["text"] = f"{latest.get('title', '')}\n\n{latest.get('text', '')}".strip()
    else:
        data["report"] = None
    if msg:
        ok = "완료" in msg.split("—")[0]
        head, _, body = msg.partition(" — ")
        data["notice"] = {"kind": "ok" if ok else "err", "head": head, "body": body}
    elif runs and runs[0]["outcome"] in FAILED:
        f = runs[0]
        what = "아래 표는 마지막 정상 결과 기준입니다." if latest else ""
        data["notice"] = {"kind": "err", "head": "최근 실행 실패", "link": "/runs",
                          "body": f"{_at(f['at'])} · {TRIGGER.get(f['trigger'], f['trigger'])} · "
                                  f"{OUTCOME.get(f['outcome'])} — {f.get('error', '')} {what}".strip()}
    return data


def run_data(run: dict) -> dict:
    report, diff = _report(run, _prev_of(run, store.load_all()))
    data = {"page": "run", "report": report, "diff": diff, "runs": [],
            "text": f"{run.get('title', '')}\n\n{run.get('text', '')}".strip()}
    if run["outcome"] in FAILED:
        data["notice"] = {"kind": "err", "head": OUTCOME[run["outcome"]], "body": run.get("error", ""), "link": "/runs"}
        data["empty_title"] = f"{_at(run['at'])} 실행 — {OUTCOME[run['outcome']]}"
        data["empty_meta"] = TRIGGER.get(run["trigger"], run["trigger"])
    return data


def _run_agent(send: bool) -> tuple[bool, str]:
    if not _running.acquire(blocking=False):
        return False, "이미 실행 중입니다. 잠시 뒤 새로고침하세요."
    try:
        cmd = [sys.executable, "-m", "agent.daily", "--trigger", "web"]
        env = dict(os.environ)
        if send:
            cmd.append("--send")
        else:
            cmd.append("--no-llm")
            env["AGENT_DRY_RUN"] = "1"  # 미리보기는 어떤 경우에도 보내지 않는다
        p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        tail = (p.stderr.strip().splitlines() or [""])[-1]
        tail = tail.split(" | ", 1)[-1]  # 로그 머리(시각·레벨) 떼기
        return p.returncode == 0, tail
    except subprocess.TimeoutExpired:
        return False, "5분 안에 끝나지 않았습니다."
    finally:
        _running.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "gpu-live"

    def _send(self, code: int, body: bytes, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # 접근 로그는 nginx 가 남긴다
        pass

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/":
            msg = (parse_qs(url.query).get("msg") or [""])[0][:300]
            return self._send(200, render(today_data(msg)))
        if path == "/create":
            # Jira 등록 (데모) — 양식은 Jira 실측값으로 만든다. 실제 등록은 하지 않는다.
            refresh = (parse_qs(url.query).get("refresh") or [""])[0] == "1"
            return self._send(200, render({"page": "create", "report": None, "runs": [],
                                           "form": jira_form.form_meta(refresh=refresh)}))
        if path == "/runs":
            return self._send(200, render({"page": "runs", "report": None, "runs": _runs_list(store.load_all())}))
        if path.startswith("/run/"):
            run = store.load(path[len("/run/"):])
            if not run:
                return self._send(404, render({"page": "run", "report": None, "runs": [],
                                               "empty_title": "기록을 찾을 수 없습니다", "empty_meta": ""}))
            return self._send(200, render(run_data(run)))
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        # 다른 사이트에서 몰래 버튼을 누르게 하는 요청(CSRF)을 막는다. 같은 출처에서 온 것만 받는다.
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        if not host or urlparse(origin).netloc != host:
            return self._send(403, "출처가 다른 요청입니다.".encode(), "text/plain; charset=utf-8")
        path = urlparse(self.path).path
        if path not in ("/action/preview", "/action/send"):
            return self._send(404, b"", "text/plain")
        ok, tail = _run_agent(send=path.endswith("send"))
        word = "발송" if path.endswith("send") else "미리보기"
        msg = f"{word} {'완료' if ok else '실패'} — {tail}"
        # 새로고침으로 다시 발송되지 않게 결과 화면으로 넘긴다 (POST → 303 → GET).
        return self._send(303, b"", "text/plain", {"Location": "/?msg=" + quote(msg)})


def main() -> None:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"listening on http://{HOST}:{PORT}", file=sys.stderr)
    srv.serve_forever()


if __name__ == "__main__":
    main()
