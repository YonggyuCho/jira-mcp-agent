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
--risk:#c62828;--warn:#a86400;--mis:#c2410c;--ok:#2e7d32;--chip:#eef1f7;--epic:#7c3aed}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141518;--card:#1e1f23;
--ink:#e8e8ea;--mute:#a0a4ab;--line:#33363c;--acc:#7aa2ff;--risk:#ff6b6b;--warn:#ffb74d;
--mis:#ff9e57;--ok:#7bd88f;--chip:#262a34;--epic:#c4a5ff}}
:root[data-theme="dark"]{--bg:#141518;--card:#1e1f23;--ink:#e8e8ea;--mute:#a0a4ab;--line:#33363c;
--acc:#7aa2ff;--risk:#ff6b6b;--warn:#ffb74d;--mis:#ff9e57;--ok:#7bd88f;--chip:#262a34;--epic:#c4a5ff}
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
th.s{cursor:pointer;user-select:none}th.s:after{content:" ↕";color:var(--line)}
th.s[data-dir="asc"]:after{content:" ▲";color:var(--acc)}th.s[data-dir="desc"]:after{content:" ▼";color:var(--acc)}
tr.flt th{padding:4px 6px;background:var(--bg)}
tr.flt select,tr.flt input{width:100%;min-width:70px;font:inherit;font-size:.82rem;padding:3px 4px;
border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.tag{display:inline-block;font-size:.78rem;font-weight:700;padding:1px 8px;border-radius:999px;white-space:nowrap;margin:1px 0}
.tag.r{background:color-mix(in srgb,var(--risk) 14%,transparent)}.tag.m{background:color-mix(in srgb,var(--mis) 14%,transparent)}
.tag.ok{background:color-mix(in srgb,var(--ok) 14%,transparent)}
.cnt{color:var(--mute);font-size:.85rem;margin-left:auto}
td.ttl{font-weight:700}  /* 제목이 가장 중요하다 (사용자 요청) */
th.ep{color:var(--epic)}  /* 상위(Epic) 열은 머리글만 보라색. 칸은 다른 열과 같게 */
.dd>button{width:100%;text-align:left;font-size:.82rem;padding:3px 6px;border-radius:6px;white-space:nowrap}
.dd.on>button{border-color:var(--acc);color:var(--acc);font-weight:600}
.pop{display:none;position:fixed;z-index:20;min-width:200px;max-height:300px;overflow:auto;background:var(--card);
border:1px solid var(--line);border-radius:8px;padding:6px;box-shadow:0 8px 24px rgba(0,0,0,.18)}
.dd.open .pop{display:block}
.pop label{display:flex;gap:8px;align-items:center;padding:4px 6px;border-radius:4px;font-weight:400;white-space:nowrap;color:var(--ink);cursor:pointer}
.pop label:hover{background:var(--chip)}.pop label.all{border-bottom:1px solid var(--line);margin-bottom:4px;font-weight:600}
"""

JS = """
// 엑셀처럼: 머리글 클릭 = 정렬, 머리글 아래 = 열별 필터(체크박스로 여러 개 선택 / 글자 포함). 상단 검색은 모든 열.
const tb=document.querySelector('table.grid tbody'),q=document.getElementById('q'),cnt=document.getElementById('cnt');
const cell=(r,i)=>r.children[i];
const val=(r,i)=>{const c=cell(r,i);return c.dataset.v!==undefined?c.dataset.v:c.textContent.trim();};
const dds=[...document.querySelectorAll('.dd')];
function apply(){if(!tb)return;const t=(q&&q.value||'').toLowerCase();let n=0;
 const ins=[...document.querySelectorAll('tr.flt input[data-col]')];
 [...tb.rows].forEach(r=>{let ok=!t||r.textContent.toLowerCase().includes(t);
  dds.forEach(dd=>{if(!ok||!dd.sel)return;const x=val(r,+dd.dataset.col);
   const vs=dd.dataset.multi?x.split('|'):[x];ok=vs.some(v=>dd.sel.has(v));});
  ins.forEach(i=>{if(ok&&i.value)ok=val(r,+i.dataset.col).toLowerCase().includes(i.value.toLowerCase());});
  r.style.display=ok?'':'none';if(ok)n++;});
 if(cnt)cnt.textContent=n+' / '+tb.rows.length+'건';}
function box(text,checked,cls){const l=document.createElement('label');if(cls)l.className=cls;
 const c=document.createElement('input');c.type='checkbox';c.checked=checked;l.append(c,document.createTextNode(text));return [l,c];}
dds.forEach(dd=>{const col=+dd.dataset.col,multi=!!dd.dataset.multi,seen=new Set();
 if(tb)[...tb.rows].forEach(r=>(multi?val(r,col).split('|'):[val(r,col)]).forEach(v=>v&&seen.add(v)));
 const vals=[...seen].sort((a,b)=>a.localeCompare(b,'ko',{numeric:true}));
 const pop=dd.querySelector('.pop'),btn=dd.querySelector('button');
 const [allL,all]=box('(전체)',true,'all');pop.append(allL);
 const items=vals.map(v=>{const [l,c]=box(v,true);c.value=v;pop.append(l);return c;});
 dd.sel=null; // null = 전체
 function sync(){const on=items.filter(c=>c.checked).map(c=>c.value);
  all.checked=on.length===items.length;all.indeterminate=on.length>0&&on.length<items.length;
  dd.sel=all.checked?null:new Set(on);dd.classList.toggle('on',!!dd.sel);
  btn.textContent=(dd.sel?(on.length?on.length+'개 선택':'선택 없음'):'(전체)')+' ▾';apply();}
 all.onchange=()=>{items.forEach(c=>c.checked=all.checked);sync();};
 items.forEach(c=>c.onchange=sync);
 btn.onclick=e=>{e.stopPropagation();const open=!dd.classList.contains('open');
  dds.forEach(x=>x.classList.remove('open'));
  if(open){const b=btn.getBoundingClientRect();pop.style.left=Math.min(b.left,innerWidth-220)+'px';
   pop.style.top=(b.bottom+4)+'px';dd.classList.add('open');}};
 pop.onclick=e=>e.stopPropagation();});
document.addEventListener('click',()=>dds.forEach(x=>x.classList.remove('open')));
addEventListener('scroll',()=>dds.forEach(x=>x.classList.remove('open')),true);
document.querySelectorAll('tr.flt input[data-col]').forEach(i=>i.oninput=apply);
document.querySelectorAll('th.s').forEach(th=>th.onclick=()=>{const c=+th.dataset.col,
 dir=th.dataset.dir==='asc'?'desc':'asc';document.querySelectorAll('th.s').forEach(h=>delete h.dataset.dir);th.dataset.dir=dir;
 const rows=[...tb.rows].sort((a,b)=>{const x=cell(a,c).dataset.s??val(a,c),y=cell(b,c).dataset.s??val(b,c);
  return (dir==='asc'?1:-1)*x.localeCompare(y,'ko',{numeric:true});});rows.forEach(r=>tb.appendChild(r));});
if(q)q.oninput=apply;apply();
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

    out.append("""<div class="card"><div class="row"><input id="q" type="search"
placeholder="전체 검색 — 키·제목·담당자·이유"><span id="cnt" class="cnt"></span></div>
<p class="small mute" style="margin:6px 0 0">머리글을 누르면 정렬, 머리글 아래 칸으로 열마다 거를 수 있습니다.</p></div>""")

    rows = []
    for r in sorted(rep["rows"], key=lambda r: (0 if r["risk"] else 1 if r["mismatch"] else 2, r["severity"],
                                                int(r["issue"]["key"].rsplit("-", 1)[1]))):
        i = r["issue"]
        cats = [c for c, on in (("위험", r["risk"]), ("불일치", r["mismatch"])) if on] or ["정상"]
        tags = "".join(f'<span class="tag {"r" if c == "위험" else "m" if c == "불일치" else "ok"}">'
                       f'{"🔴" if c == "위험" else "🟠" if c == "불일치" else "✅"} {c}</span> ' for c in cats)
        rank = 0 if r["risk"] else 1 if r["mismatch"] else 2
        reasons = "".join(f'<li class="r">{esc(x)}</li>' for x in r["risk"])
        reasons += "".join(f'<li class="m">{esc(x)}</li>' for x in r["mismatch"])
        if not reasons:
            reasons = '<li class="ok">정상</li>'
        mark = "🆕 " if i["key"] in d["new"] else ""
        num = i["key"].rsplit("-", 1)[1]
        rows.append(f"""<tr><td data-v="{'|'.join(cats)}" data-s="{rank}{r['severity']}">{tags}</td>
<td class="ep">{esc(i.get('parent_title') or '-')}</td>
<td class="k" data-v="{esc(i['key'])}" data-s="{int(num):08d}">{mark}<a href="{esc(i['url'])}" target="_blank" rel="noopener">{esc(i['key'])}</a></td>
<td class="ttl">{esc(i['summary'])}</td><td>{esc(i['kind'])}</td>
<td>{esc(i['status'])}</td><td>{esc(i['assignee'] or '없음')}</td><td>{esc(i['duedate'] or '-')}</td>
<td>{esc(i['compliance'] or '-')}</td><td><ul class="rs">{reasons}</ul></td></tr>""")
    # (머리글, 필터 종류). 필터 종류는 열 이름에 붙여 둔다 — 열 순서를 바꿔도 엉뚱한 열을 거르지 않게.
    cols = [("판정", "multi"), ("상위 (Epic)", "select"), ("키", "input"), ("제목", "input"), ("유형", "select"),
            ("상태", "select"), ("담당", "select"), ("기한", None), ("일정 준수", "select"), ("이유", "input")]
    flt = []
    for n, (_, k) in enumerate(cols):
        if k in ("select", "multi"):
            flt.append(f'<th><div class="dd" data-col="{n}"{" data-multi=1" if k == "multi" else ""}>'
                       f'<button type="button">(전체) ▾</button><div class="pop"></div></div></th>')
        elif k == "input":
            flt.append(f'<th><input data-col="{n}" placeholder="포함"></th>')
        else:
            flt.append("<th></th>")
    th = "".join(f'<th class="s{" ep" if h.startswith("상위") else ""}" data-col="{n}">{h}</th>' for n, (h, _) in enumerate(cols))
    out.append(f"""<div class="card tw"><table class="grid"><thead><tr>{th}</tr><tr class="flt">{''.join(flt)}</tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>""")
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
