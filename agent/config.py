"""에이전트 설정. jira_mcp.config 와 같은 .env 를 읽는다."""
from __future__ import annotations

import os
from dataclasses import dataclass

from jira_mcp.config import ConfigError  # noqa: F401  (호출부에서 같이 잡으라고 재노출)

# teams = Workflows(Adaptive Card, 현행) / teams-legacy = O365 커넥터(MessageCard, 퇴역)
FORMATS = {"slack", "teams", "teams-legacy", "dooray", "raw"}


@dataclass(frozen=True)
class AgentConfig:
    project: str
    solution: str
    webhook_url: str
    webhook_format: str
    model: str
    effort: str
    due_soon_days: int
    dry_run: bool

    @property
    def can_notify(self) -> bool:
        return bool(self.webhook_url) and not self.dry_run


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def load() -> AgentConfig:
    fmt = os.environ.get("AGENT_WEBHOOK_FORMAT", "slack").strip().lower()
    if fmt not in FORMATS:
        raise ConfigError(
            f"AGENT_WEBHOOK_FORMAT 는 {' | '.join(sorted(FORMATS))} 중 하나여야 합니다 (현재: {fmt})"
        )
    try:
        days = int(os.environ.get("AGENT_DUE_SOON_DAYS", "3"))
    except ValueError as exc:
        raise ConfigError(f"AGENT_DUE_SOON_DAYS 가 숫자가 아닙니다: {exc}") from exc

    project = os.environ.get("AGENT_PROJECT", "").strip()
    solution = os.environ.get("AGENT_SOLUTION", "").strip()
    if not project or not solution:
        raise ConfigError(
            "AGENT_PROJECT 와 AGENT_SOLUTION 을 .env 에 설정하세요 "
            "(예: AGENT_PROJECT=ABC / AGENT_SOLUTION=제품명). "
            ".env.example 참고."
        )

    return AgentConfig(
        project=project,
        solution=solution,
        webhook_url=os.environ.get("AGENT_WEBHOOK_URL", "").strip(),
        webhook_format=fmt,
        # 모델을 바꾸는 건 비용 결정이라 기본값을 낮추지 않는다. 필요하면 .env 에서 지정.
        model=os.environ.get("AGENT_MODEL", "claude-opus-5").strip(),
        # 요약은 짧고 단순해서 effort 를 낮춰도 품질이 유지된다. 비용 조절 손잡이.
        effort=os.environ.get("AGENT_EFFORT", "low").strip(),
        due_soon_days=days,
        # 기본이 dry-run 이다. 실제 발송은 .env 에서 명시적으로 켠다.
        dry_run=_flag("AGENT_DRY_RUN", "1"),
    )
