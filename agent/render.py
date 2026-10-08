"""Report 를 사람이 읽는 텍스트로. LLM 이 죽어도 이건 항상 나온다."""
from __future__ import annotations

import os

from .collect import MISMATCH, RISK, Report

SEVERITY_MARK = {0: "🔴", 1: "🔴", 2: "🟡", 3: "🟠"}


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


def _split(report: Report):
    risk = group([f for f in report.findings if f.category == RISK])
    mismatch = group([f for f in report.findings if f.category == MISMATCH])
    return risk, mismatch


def headline(report: Report) -> str:
    risk, mismatch = _split(report)
    if not risk and not mismatch:
        return f"{report.solution} {report.today} — 이상 없음 ({len(report.issues)}건)"
    return f"{report.solution} {report.today} — 위험 {len(risk)} · 불일치 {len(mismatch)}"


def _section(title: str, grouped) -> list[str]:
    out = ["", f"■ {title} {len(grouped)}건"]
    # 한 이슈가 여러 이유로 걸릴 수 있다. 이슈는 한 번만 찍고 이유를 아래에 모은다.
    for issue, reasons, severity in grouped:
        mark = SEVERITY_MARK.get(severity, "⚪")
        who = issue.assignee or "담당자 없음"
        out.append(f"  {mark} {issue.key}  {issue.summary[:40]}  [{issue.status} · {who}]")
        out += [f"      · {r}" for r in reasons]
        out.append(f"      {issue.url}")
    return out


def body(report: Report) -> str:
    """헤드라인은 넣지 않는다. 메신저가 제목란에 따로 찍기 때문에 넣으면 두 번 나온다."""
    out = []

    counts = report.counts
    total = len(report.issues)
    breakdown = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    out.append(f"전체 {total}건 — {breakdown}")

    risk, mismatch = _split(report)
    # 급한 것 먼저, 같은 급이면 이슈 번호 순. 매일 같은 순서여야 비교하기 쉽다.
    order = lambda g: (g[2], int(g[0].key.rsplit("-", 1)[1]))  # noqa: E731
    if risk:
        out += _section("위험", sorted(risk, key=order))
    if mismatch:
        out += _section("불일치 / 오기입", sorted(mismatch, key=order))
    if not risk and not mismatch:
        out += ["", "■ 위험·불일치 없음"]

    if report.hygiene:
        out += ["", "■ 데이터 점검"]
        out += [f"  · {h}" for h in report.hygiene]

    if report.skipped:
        out += ["", "제외: " + ", ".join(f"{k} {v}건" for k, v in report.skipped.items())]

    if web := os.environ.get("AGENT_WEB_URL", "").strip():
        out += ["", f"웹에서 보기: {web}"]

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
            f"- [{f.category}] {i.key} | {i.kind} | 상태 {i.status} | 담당 {i.assignee or '없음'} "
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
