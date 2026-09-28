"""웹훅 전송. 메신저마다 본문 모양이 달라 포맷을 갈아끼운다.

어느 메신저를 쓸지는 .env 의 AGENT_WEBHOOK_FORMAT 하나로 정한다.
나중에 메신저를 바꿔도 여기 한 곳만 본다.

Teams 주의 — 웹훅 종류가 둘이고 받는 모양이 다르다.
  · Workflows(Power Automate) : 지금 발급되는 것. Adaptive Card 를 받는다.
    URL 이 ...logic.azure.com/workflows/... 형태다.            -> teams
  · Office 365 커넥터          : 퇴역했다. MessageCard 를 받는다.
    URL 이 ...webhook.office.com/webhookb2/... 형태다.         -> teams-legacy
URL 모양으로 어느 쪽인지 알 수 있어서, 설정이 어긋나면 자동으로 맞춘다.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

LEGACY_HOST = "webhook.office.com"
WORKFLOW_HOST = "logic.azure.com"


class NotifyError(RuntimeError):
    pass


def _teams_card(title: str, text: str) -> dict:
    """Workflows 웹훅용 Adaptive Card.

    표 정렬이 깨지지 않게 본문은 Monospace 로 찍는다.
    Adaptive Card 에는 코드블록이 없어서 fontType 이 유일한 방법이다.
    """
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.4",
                "body": [
                    {"type": "TextBlock", "text": title, "weight": "Bolder",
                     "size": "Medium", "wrap": True},
                    {"type": "TextBlock", "text": text, "fontType": "Monospace",
                     "wrap": True},
                ],
            },
        }],
    }


def _payload(fmt: str, title: str, text: str, bot: str) -> dict:
    if fmt == "slack":
        # Slack 호환(Mattermost, 다수의 사내 훅 포함). 코드블록으로 표 정렬을 지킨다.
        return {"text": f"*{title}*\n```\n{text}\n```"}
    if fmt == "teams":
        return _teams_card(title, text)
    if fmt == "teams-legacy":
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": title,
            "title": title,
            "text": "<pre>" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>",
        }
    if fmt == "dooray":
        return {"botName": bot, "text": title, "attachments": [{"text": text}]}
    return {"title": title, "text": text}  # raw


def _resolve(fmt: str, url: str) -> str:
    """URL 이 말해주는 쪽을 따른다. 설정이 어긋난 채 조용히 실패하는 걸 막는다."""
    if fmt == "teams" and LEGACY_HOST in url:
        log.warning("URL 이 Office 365 커넥터라 teams-legacy 로 보냅니다 "
                    "(커넥터는 퇴역했습니다 — Workflows 훅으로 옮기세요)")
        return "teams-legacy"
    if fmt == "teams-legacy" and WORKFLOW_HOST in url:
        log.warning("URL 이 Workflows 훅이라 teams 로 보냅니다")
        return "teams"
    return fmt


def send(url: str, fmt: str, title: str, text: str,
         bot: str = "Jira Agent", timeout: float = 15.0) -> None:
    if not url:
        raise NotifyError("AGENT_WEBHOOK_URL 이 비어 있습니다.")
    fmt = _resolve(fmt, url)
    try:
        r = httpx.post(url, json=_payload(fmt, title, text, bot), timeout=timeout)
    except httpx.HTTPError as exc:
        raise NotifyError(f"웹훅 연결 실패: {exc}") from exc
    if r.status_code >= 400:
        hint = ""
        if fmt.startswith("teams") and r.status_code in (400, 404):
            hint = ("\n힌트: Teams 웹훅 종류와 페이로드가 맞는지 확인하세요. "
                    "지금 발급되는 훅은 Workflows(Adaptive Card)이고, "
                    "퇴역한 O365 커넥터는 MessageCard 입니다.")
        raise NotifyError(f"웹훅 거부 HTTP {r.status_code}: {r.text[:200]}{hint}")
    # Workflows 훅은 수락 시 202 를 준다. 본문이 비어 있어도 정상이다.
    log.info("웹훅 응답 HTTP %s", r.status_code)
