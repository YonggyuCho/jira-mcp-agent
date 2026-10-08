"""에이전트 웹 화면. 표준 라이브러리만 쓴다 — 서버를 옮길 때 설치할 것을 늘리지 않으려고.

  python -m web.server            # 127.0.0.1:8090

밖에서 바로 붙지 않는다. nginx 가 HTTPS 와 로그인(basic auth)을 맡고 여기로 넘긴다.
그래서 127.0.0.1 에만 묶는다. 판정은 하지 않는다 — agent 가 남긴 runs/*.json 을 보여 주고,
버튼은 cron 과 똑같은 명령(python -m agent.daily)을 실행할 뿐이다.
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from agent import store

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEB_PORT", "8090"))
_running = threading.Lock()  # 발송·미리보기가 겹치지 않게

OUTCOME = {
    "sent": ("발송", "ok"), "dry-run": ("미리보기", "mute"),
    "collect-failed": ("수집 실패", "bad"), "send-failed": ("발송 실패", "bad"),
}
TRIGGER = {"cron": "자동", "web": "웹", "manual": "수동"}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c1e21;--mute:#65676b;--line:#e1e4e8;--acc:#2f5fd0;
--risk:#c62828;--warn:#a86400;--mis:#c2410c;--ok:#2e7d32;--chip:#eef1f7}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141518;--card:#1e1f23;
--ink:#e8e8ea;--mute:#a0a4ab;--line:#33363c;--acc:#7aa2ff;--risk:#ff6b6b;--warn:#ffb74d;
--mis:#ff9e57;--ok:#7bd88f;--chip:#262a34}}
:root[data-theme="dark"]{--bg:#141518;--card:#1e1f23;--ink:#e8e8ea;--mute:#a0a4ab;--line:#33363c;
--acc:#7aa2ff;--risk:#ff6b6b;--warn:#ffb74d;--mis:#ff9e57;--ok:#7bd88f;--chip:#262a34}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line)}
.wrap{max-width:1100px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:4px;align-items:center;height:52px;overflow-x:auto}
nav b{margin-right:12px;white-space:nowrap}
nav a{color:var(--mute);text-decoration:none;padding:6px 10px;border-radius:6px;white-space:nowrap}
nav a.on{color:var(--ink);background:var(--chip);font-weight:600}
main{padding:20px 0 60px}
h1{font-size:1.25rem;margin:0 0 4px}.sub{color:var(--mute);margin:0 0 16px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:12px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat .n{font-size:1.6rem;font-weight:700}.stat .l{color:var(--mute);font-size:.85rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);
padding:8px 14px;border-radius:8px;cursor:pointer}button.pri{background:var(--acc);color:#fff;border-color:var(--acc)}
button:disabled{opacity:.5;cursor:wait}
.chip{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:4px 12px;cursor:pointer;color:var(--mute)}
.chip.on{background:var(--chip);color:var(--ink);font-weight:600;border-color:var(--acc)}
input[type=search]{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:8px;
background:var(--card);color:var(--ink);min-width:200px;flex:1}
.tw{overflow-x:auto}table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mute);font-weight:600;font-size:.85rem;white-space:nowrap}
td.k a{color:var(--acc);text-decoration:none;font-weight:600;white-space:nowrap}
.r{color:var(--risk)}.w{color:var(--warn)}.m{color:var(--mis)}.ok{color:var(--ok)}.mute{color:var(--mute)}.bad{color:var(--risk);font-weight:600}
ul.rs{margin:0;padding-left:16px}ul.rs li{margin:1px 0}
.small{font-size:.85rem}.new{color:var(--risk);font-weight:600}.gone{color:var(--ok);font-weight:600}
pre{white-space:pre-wrap;background:var(--chip);padding:12px;border-radius:8px;font-size:.85rem}
.msg{padding:10px 14px;border-radius:8px;background:var(--chip);margin:12px 0}
"""

JS = """
const chips=[...document.querySelectorAll('.chip')],q=document.getElementById('q');
function apply(){const on=chips.filter(c=>c.classList.contains('on')).map(c=>c.dataset.f);
 const t=(q&&q.value||'').toLowerCase();
 document.querySelectorAll('tr[data-f]').forEach(r=>{
  const okF=!on.length||on.some(f=>r.dataset.f.includes(f));
  const okT=!t||r.textContent.toLowerCase().includes(t);
  r.style.display=okF&&okT?'':'none';});}
chips.forEach(c=>c.onclick=()=>{c.classList.toggle('on');apply();});
if(q)q.oninput=apply;
document.querySelectorAll('form.act').forEach(f=>f.onsubmit=e=>{
 if(f.dataset.confirm&&!confirm(f.dataset.confirm)){e.preventDefault();return;}
 f.querySelectorAll('button').forEach(b=>{b.disabled=true;b.textContent='실행 중…';});});
"""


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def page(title: str, tab: str, body: str) -> bytes:
    tabs = [("/", "오늘"), ("/runs", "실행 기록")]
    nav = "".join(f'<a href="{h}" class="{"on" if h == tab else ""}">{t}</a>' for h, t in tabs)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>
<style>{CSS}</style></head><body><header><div class="wrap"><nav><b>GPU Live 에이전트</b>{nav}</nav></div></header>
<main class="wrap">{body}</main><script>{JS}</script></body></html>""".encode()


def _flag(row) -> str:
    if row["risk"]:
        return "risk"
    if row["mismatch"]:
        return "mismatch"
    return "ok"


def report_view(run: dict, prev: dict | None, msg: str = "") -> str:
    rep = run.get("report")
    label, cls = OUTCOME.get(run["outcome"], (run["outcome"], "mute"))
    head = (f'<h1>{esc(run.get("title") or "실행 결과")}</h1>'
            f'<p class="sub">{esc(run["at"].replace("T", " "))} · {TRIGGER.get(run["trigger"], run["trigger"])} · '
            f'<span class="{cls}">{label}</span>'
            + (f' · 웹훅 HTTP {run["webhook_status"]}' if run.get("webhook_status") else "") + "</p>")
    actions = """<div class="row">
<form class="act" method="post" action="/action/preview"><button>미리보기 (Jira 새로 읽기, 발송 안 함)</button></form>
<form class="act" method="post" action="/action/send" data-confirm="지금 Teams 로 리포트를 보낼까요?">
<button class="pri">지금 Teams 로 발송</button></form></div>"""
    out = [head, f'<div class="msg">{esc(msg)}</div>' if msg else "", actions]
    if run.get("error"):
        out.append(f'<div class="card bad">오류: {esc(run["error"])}</div>')
    if not rep:
        return "".join(out)

    d = store.diff(run, prev)
    out.append(f"""<div class="stats">
<div class="stat"><div class="n r">{rep['risk']}</div><div class="l">위험</div></div>
<div class="stat"><div class="n m">{rep['mismatch']}</div><div class="l">불일치 / 오기입</div></div>
<div class="stat"><div class="n ok">{rep['ok']}</div><div class="l">정상</div></div>
<div class="stat"><div class="n">{rep['total']}</div><div class="l">전체 · {esc(', '.join(f'{k} {v}' for k, v in sorted(rep['counts'].items())))}</div></div>
</div>""")
    if d["base"]:
        new = ", ".join(d["new"]) or "없음"
        gone = ", ".join(d["resolved"]) or "없음"
        out.append(f'<div class="card small">이전 리포트({esc(d["base"].replace("T", " "))}) 대비 — '
                   f'<span class="new">새로 걸림: {esc(new)}</span> · <span class="gone">해결: {esc(gone)}</span></div>')

    out.append("""<div class="card"><div class="row">
<span class="chip" data-f="risk">🔴 위험</span><span class="chip" data-f="mismatch">🟠 불일치</span>
<span class="chip" data-f="ok">✅ 정상</span><input id="q" type="search" placeholder="키·제목·담당자 검색"></div></div>""")

    rows = []
    for r in sorted(rep["rows"], key=lambda r: (0 if r["risk"] else 1 if r["mismatch"] else 2, r["severity"],
                                                int(r["issue"]["key"].rsplit("-", 1)[1]))):
        i = r["issue"]
        flags = " ".join(f for f in ("risk", "mismatch") if r[f]) or "ok"
        reasons = "".join(f'<li class="{"r" if r["severity"] <= 1 else "w"}">{esc(x)}</li>' for x in r["risk"])
        reasons += "".join(f'<li class="m">{esc(x)}</li>' for x in r["mismatch"])
        if not reasons:
            reasons = '<li class="ok">정상</li>'
        mark = "🆕 " if i["key"] in d["new"] else ""
        rows.append(f"""<tr data-f="{flags}"><td class="k">{mark}<a href="{esc(i['url'])}" target="_blank" rel="noopener">{esc(i['key'])}</a></td>
<td>{esc(i['summary'])}<div class="small mute">{esc(i['kind'])}</div></td><td>{esc(i['status'])}</td>
<td>{esc(i['assignee'] or '없음')}</td><td>{esc(i['duedate'] or '-')}</td><td>{esc(i['compliance'] or '-')}</td>
<td><ul class="rs">{reasons}</ul></td></tr>""")
    out.append(f"""<div class="card tw"><table><thead><tr><th>키</th><th>제목</th><th>상태</th><th>담당</th>
<th>기한</th><th>일정 준수</th><th>판정 · 이유</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>""")
    if rep.get("skipped"):
        out.append('<p class="small mute">제외: ' + esc(", ".join(f"{k} {v}건" for k, v in rep["skipped"].items())) + "</p>")
    if run.get("text"):
        out.append(f'<details class="card"><summary>Teams 로 보낸(보낼) 본문</summary><pre>{esc(run["title"])}\n\n{esc(run["text"])}</pre></details>')
    return "".join(out)


def runs_view() -> str:
    rows = []
    for r in store.load_all()[:200]:
        label, cls = OUTCOME.get(r["outcome"], (r["outcome"], "mute"))
        rep = r.get("report") or {}
        cnt = f"위험 {rep.get('risk', '-')} · 불일치 {rep.get('mismatch', '-')}" if rep else "-"
        rows.append(f"""<tr><td><a href="/run/{esc(r['id'])}">{esc(r['at'].replace('T', ' '))}</a></td>
<td>{TRIGGER.get(r['trigger'], esc(r['trigger']))}</td><td class="{cls}">{label}</td><td>{cnt}</td>
<td>{esc(r.get('webhook_status') or '')}</td><td class="small">{esc((r.get('error') or '')[:120])}</td></tr>""")
    return ("<h1>실행 기록</h1><p class='sub'>자동(평일 09:00)·웹·수동 실행이 전부 남는다. "
            "웹훅 202 는 Teams 가 받았다는 뜻이고, 채팅 게시 여부는 Power Automate 실행 기록에서 본다.</p>"
            f"""<div class="card tw"><table><thead><tr><th>시각</th><th>출처</th><th>결과</th><th>건수</th>
<th>웹훅</th><th>오류</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan=6>기록 없음</td></tr>'}</tbody></table></div>""")


def _prev_of(run: dict, runs: list[dict]) -> dict | None:
    """비교 기준: 이 실행보다 앞선, 결과가 있는 실행 중 '다른 날짜' 의 마지막 것 (=어제 리포트)."""
    day = run["at"][:10]
    for r in runs:
        if r["at"] < run["at"] and r.get("report") and r["at"][:10] != day:
            return r
    return None


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
        msg = (parse_qs(url.query).get("msg") or [""])[0][:300]
        if path == "/":
            runs = store.load_all()
            latest = next((r for r in runs if r.get("report")), None)
            if latest is None:
                return self._send(200, page("GPU Live", "/", "<h1>아직 실행 기록이 없습니다</h1>"
                                            "<p>미리보기를 눌러 첫 기록을 만드세요.</p>"
                                            + report_view({"outcome": "dry-run", "trigger": "web", "at": "-"}, None)))
            # 가장 최근 실행이 실패였다면 맨 위에 알린다
            if not msg and runs and runs[0]["outcome"] in ("collect-failed", "send-failed"):
                msg = f"가장 최근 실행({runs[0]['at'].replace('T', ' ')})이 실패했습니다: {runs[0].get('error', '')}"
            return self._send(200, page("GPU Live", "/", report_view(latest, _prev_of(latest, runs), msg)))
        if path == "/runs":
            return self._send(200, page("실행 기록", "/runs", runs_view()))
        if path.startswith("/run/"):
            run = store.load(path[len("/run/"):])
            if not run:
                return self._send(404, page("없음", "", "<h1>기록을 찾을 수 없습니다</h1>"))
            return self._send(200, page(run.get("title") or "실행", "", report_view(run, _prev_of(run, store.load_all()))))
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        return self._send(404, page("없음", "", "<h1>404</h1>"))

    def do_POST(self):
        # 다른 사이트에서 몰래 버튼을 누르게 하는 요청(CSRF)을 막는다. 같은 출처에서 온 것만 받는다.
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        if not host or urlparse(origin).netloc != host:
            return self._send(403, page("거부", "", "<h1>403</h1><p>출처가 다른 요청입니다.</p>"))
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
