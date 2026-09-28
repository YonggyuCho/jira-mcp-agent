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
from . import collect, notify, render, summarize

log = logging.getLogger("agent.daily")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="솔루션 일일 현황 취합")
    ap.add_argument("--send", action="store_true", help="dry-run 을 무시하고 실제 발송")
    ap.add_argument("--no-llm", action="store_true", help="LLM 요약을 건너뛴다")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        stream=sys.stderr,
    )

    try:
        cfg = agent_config.load()
        report = collect.collect(cfg.project, cfg.solution,
                                 due_soon_days=cfg.due_soon_days)
    except (jira_config.ConfigError, client.JiraError) as exc:
        # 수집 실패는 조용히 넘기면 안 된다. 알림이 안 온 건지 이상이 없는 건지 구분돼야 한다.
        log.error("수집 실패 — 알림을 보내지 않습니다: %s", exc)
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
        return 0

    try:
        notify.send(cfg.webhook_url, cfg.webhook_format, title, text, bot=cfg.solution)
    except notify.NotifyError as exc:
        log.error("발송 실패: %s", exc)
        print(f"{title}\n\n{text}")  # 최소한 로그에는 남긴다
        return 1

    # 헤드라인과 같은 기준(이슈 단위)으로 센다. 한 이슈가 여러 이유로 걸릴 수 있다.
    log.info("발송 완료 — 조치 필요 %d건", len(render.group(report.findings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
