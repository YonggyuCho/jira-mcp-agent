"""실행 결과 저장. 웹 화면·이력·'어제 대비' 가 전부 여기서 나온다.

한 번 실행할 때마다 runs/<날짜>/<시각>.json 하나를 남긴다. 성공·실패 모두 남긴다 —
"왜 안 왔어?" 에 답하려면 실패한 실행도 기록돼 있어야 한다.

이 파일들에는 이슈 키·담당자 이름이 들어간다. 저장소는 공개라 runs/ 는 .gitignore 로 뺀다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from .collect import MISMATCH, RISK, Report

ROOT = Path(__file__).resolve().parent.parent
RUNS = Path(os.environ.get("AGENT_RUNS_DIR", ROOT / "runs"))


def _issue(i) -> dict:
    return {
        "key": i.key, "summary": i.summary, "status": i.status, "kind": i.kind,
        "assignee": i.assignee, "duedate": i.duedate.isoformat() if i.duedate else None,
        "compliance": i.compliance, "parent": i.parent, "done": i.is_done, "url": i.url,
    }


def snapshot(report: Report) -> dict:
    """화면에 필요한 것만. 판정 결과(이유 포함)와 이슈 목록."""
    by_key: dict[str, dict] = {}
    for f in report.findings:
        row = by_key.setdefault(f.issue.key, {"issue": _issue(f.issue), "risk": [], "mismatch": [],
                                              "severity": 9, "in_scope": True})
        row["risk" if f.category == RISK else "mismatch"].append(f.reason)
        row["severity"] = min(row["severity"], f.severity)
    keys = {i.key for i in report.issues}
    for k, row in by_key.items():
        row["in_scope"] = k in keys
    for i in report.issues:
        by_key.setdefault(i.key, {"issue": _issue(i), "risk": [], "mismatch": [],
                                  "severity": 9, "in_scope": True})
    rows = sorted(by_key.values(), key=lambda r: int(r["issue"]["key"].rsplit("-", 1)[1]))
    return {
        "today": report.today.isoformat(),
        "solution": report.solution,
        "total": len(report.issues),
        "counts": report.counts,
        "risk": sum(1 for r in rows if r["risk"]),
        "mismatch": sum(1 for r in rows if r["mismatch"]),
        "ok": sum(1 for r in rows if r["in_scope"] and not r["risk"] and not r["mismatch"]),
        "skipped": report.skipped,
        "hygiene": report.hygiene,
        "rows": rows,
    }


def save(*, trigger: str, outcome: str, title: str = "", text: str = "",
         report: Report | None = None, error: str = "", webhook_status: int | None = None) -> Path:
    """outcome: sent / dry-run / collect-failed / send-failed"""
    now = dt.datetime.now()
    path = RUNS / now.strftime("%Y-%m-%d") / f"{now.strftime('%H%M%S')}-{trigger}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "at": now.isoformat(timespec="seconds"),
        "trigger": trigger,          # cron / web / manual
        "outcome": outcome,
        "title": title,
        "text": text,
        "error": error,
        "webhook_status": webhook_status,
        "report": snapshot(report) if report else None,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_all() -> list[dict]:
    """최신순. 각 항목에 id(상대 경로)를 붙인다."""
    out = []
    for p in sorted(RUNS.glob("*/*.json"), reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d["id"] = f"{p.parent.name}/{p.stem}"
        out.append(d)
    return out


def load(run_id: str) -> dict | None:
    # run_id 는 '<날짜>/<파일명>' 만 허용한다. 경로 밖으로 나가는 것을 막는다.
    date, _, stem = run_id.partition("/")
    if not (date and stem) or any(c in run_id for c in ("..", "\\")) or run_id.count("/") != 1:
        return None
    p = RUNS / date / f"{stem}.json"
    if not p.is_file():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    d["id"] = run_id
    return d


def diff(cur: dict, prev: dict | None) -> dict:
    """이전 리포트 대비 새로 걸린 것 / 풀린 것 (이슈 키 기준)."""
    def flagged(d):
        rows = ((d or {}).get("report") or {}).get("rows", [])
        return {r["issue"]["key"] for r in rows if r["risk"] or r["mismatch"]}
    if not prev or not prev.get("report"):
        return {"new": [], "resolved": [], "base": None}
    a, b = flagged(prev), flagged(cur)
    return {"new": sorted(b - a), "resolved": sorted(a - b), "base": prev.get("at")}


__all__ = ["save", "load", "load_all", "diff", "RISK", "MISMATCH"]
