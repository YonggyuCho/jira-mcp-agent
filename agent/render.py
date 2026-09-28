"""Report 를 사람이 읽는 텍스트로. LLM 이 죽어도 이건 항상 나온다."""
from __future__ import annotations

from .collect import Report

SEVERITY_MARK = {0: "🔴", 1: "🔴", 2: "🟡", 3: "⚪"}


def group(findings):
    """같은 이슈의 지적을 하나로 묶는다. (issue, [reason...], 가장 급한 severity)"""
    order: list[str] = []
    bucket: dict[str, list] = {}
    for f in findings:
        if f.issue.key not in bucket:
            bucket[f.issue.key] = []
            order.append(f.issue.key)
        bucket[f.issue.key].append(f)
    return [
        (bucket[k][0].issue, [f.reason for f in bucket[k]], min(f.severity for f in bucket[k]))
        for k in order
    ]


def headline(report: Report) -> str:
    n = len(group(report.findings))
    urgent = sum(1 for _, _, sev in group(report.findings) if sev <= 1)
    if not n:
        return f"{report.solution} {report.today} — 조치 필요 없음 ({len(report.issues)}건 정상)"
    if urgent:
        return f"{report.solution} {report.today} — 조치 필요 {n}건 (긴급 {urgent})"
    return f"{report.solution} {report.today} — 확인 {n}건"


def body(report: Report) -> str:
    out = [headline(report), ""]

    counts = report.counts
    total = len(report.issues)
    breakdown = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    out.append(f"전체 {total}건 — {breakdown}")

    if report.findings:
        out += ["", "■ 조치 필요"]
        # 한 이슈가 여러 이유로 걸릴 수 있다(기한 임박 + 담당자 없음). 묶어서 한 번만 찍는다.
        for issue, reasons, severity in group(report.findings):
            mark = SEVERITY_MARK.get(severity, "⚪")
            who = issue.assignee or "담당자 없음"
            out.append(f"  {mark} {issue.key}  {' / '.join(reasons)}")
            out.append(f"      {issue.summary[:50]}  [{who}]")
            out.append(f"      {issue.url}")
    else:
        out += ["", "■ 조치 필요 없음 — Delayed 0건, 기한 초과 0건"]

    if report.hygiene:
        out += ["", "■ 데이터 점검"]
        out += [f"  · {h}" for h in report.hygiene]

    return "\n".join(out)


def for_llm(report: Report) -> str:
    """요약 모델에게 넘길 사실 목록. 여기 없는 건 모델이 알 수 없다."""
    lines = [f"오늘: {report.today}", f"전체: {len(report.issues)}건"]
    lines.append("상태별: " + ", ".join(f"{k} {v}" for k, v in sorted(report.counts.items())))

    lines.append("")
    lines.append("조치 필요 목록:" if report.findings else "조치 필요: 없음")
    for f in report.findings:
        i = f.issue
        lines.append(
            f"- {i.key} | {i.kind} | 상태 {i.status} | 담당 {i.assignee or '없음'} "
            f"| 기한 {i.duedate or '없음'} | 일정준수 {i.compliance or '공란'} "
            f"| 사유: {f.reason} | 제목: {i.summary[:60]}"
        )

    if report.hygiene:
        lines += ["", "데이터 점검:"] + [f"- {h}" for h in report.hygiene]

    lines += ["", "진행 중인 이슈:"]
    for i in report.open_issues:
        lines.append(
            f"- {i.key} | 기한 {i.duedate or '없음'} | 담당 {i.assignee or '없음'} "
            f"| {i.summary[:60]}"
        )
    return "\n".join(lines)
