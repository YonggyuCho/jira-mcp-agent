"""매일 1회 실행 진입점.

  python -m agent.daily            # .env 의 AGENT_DRY_RUN 을 따른다 (기본 dry-run)
  python -m agent.daily --send     # 이번 실행만 실제 발송
  python -m agent.daily --no-llm   # 요약 없이 표만

cron 예:
  0 9 * * 1-5  cd /opt/jira-mcp && .venv/bin/python -m agent.daily >> /var/log/gpu-live.log 2>&1
"""
from __future__ import annotations

import argparse
import logging
import sys

from jira_mcp import client, config as jira_config

from . import config as agent_config
from . import collect, notify, render, store, summarize

log = logging.getLogger("agent.daily")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="솔루션 일일 현황 취합")
    ap.add_argument("--send", action="store_true", help="dry-run 을 무시하고 실제 발송")
    ap.add_argument("--no-llm", action="store_true", help="LLM 요약을 건너뛴다")
    ap.add_argument("--trigger", default="manual", choices=["cron", "web", "manual"],
                    help="실행 기록에 남길 출처")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        stream=sys.stderr,
    )

    try:
        cfg = agent_config.load()
        report = collect.collect(cfg.project, cfg.solution,
                                 due_soon_days=cfg.due_soon_days, epic=cfg.epic)
    except (jira_config.ConfigError, client.JiraError) as exc:
        # 수집 실패는 조용히 넘기면 안 된다. 알림이 안 온 건지 이상이 없는 건지 구분돼야 한다.
        log.error("수집 실패 — 알림을 보내지 않습니다: %s", exc)
        _save(args.trigger, "collect-failed", error=str(exc))
        return 1

    text = render.body(report)
    if not args.no_llm:
        if note := summarize.summarize(report, cfg.model, cfg.effort):
            text = f"{note}\n\n{text}"

    title = render.headline(report)
    send = args.send or cfg.can_notify

    if not send:
        reason = "AGENT_WEBHOOK_URL 미설정" if not cfg.webhook_url else "dry-run"
        log.info("발송하지 않음 (%s). 아래는 보낼 내용입니다.", reason)
        print(f"{title}\n\n{text}")
        _save(args.trigger, "dry-run", title=title, text=text, report=report)
        return 0

    try:
        status = notify.send(cfg.webhook_url, cfg.webhook_format, title, text, bot=cfg.solution)
    except notify.NotifyError as exc:
        log.error("발송 실패: %s", exc)
        print(f"{title}\n\n{text}")  # 최소한 로그에는 남긴다
        _save(args.trigger, "send-failed", title=title, text=text, report=report, error=str(exc))
        return 1

    _save(args.trigger, "sent", title=title, text=text, report=report, webhook_status=status)

    log.info("발송 완료 — %s", render.headline(report))
    return 0


def _save(trigger: str, outcome: str, **kw) -> None:
    """실행 기록 저장. 기록이 실패해도 발송 결과(종료 코드)는 바꾸지 않는다."""
    try:
        store.save(trigger=trigger, outcome=outcome, **kw)
    except Exception:  # 디스크 문제로 매일 알림이 실패 처리되면 안 된다
        log.exception("실행 기록 저장 실패")


if __name__ == "__main__":
    raise SystemExit(main())
