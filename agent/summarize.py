"""LLM 한 줄 요약. 하이브리드에서 유일하게 모델이 쓰이는 지점.

**실패해도 리포트는 나간다.** 요약은 부가 가치지 본문이 아니다.
API 장애나 키 만료로 매일 오는 알림이 끊기면 안 된다.
"""
from __future__ import annotations

import logging

from .collect import Report
from .render import for_llm

log = logging.getLogger(__name__)

SYSTEM = """\
너는 {solution} 솔루션 담당자에게 매일 아침 한 문단을 써 주는 조수다.

받은 사실 목록만 근거로 쓴다. 목록에 없는 원인·추측·조언을 지어내지 않는다.
3~4문장 안에서, 오늘 사람이 실제로 움직여야 하는 것이 무엇인지 먼저 말한다.
조치할 것이 없으면 그렇다고 한 문장으로 끝낸다. 목록을 다시 나열하지 않는다.
이슈 키는 그대로 쓴다. 인사말과 맺음말은 쓰지 않는다.\
"""


def summarize(report: Report, model: str, effort: str) -> str | None:
    """요약 문자열, 또는 실패 시 None."""
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic 패키지가 없어 요약을 건너뜁니다")
        return None

    try:
        client = anthropic.Anthropic(timeout=60.0)
        resp = client.messages.create(
            model=model,
            # 웹훅 메시지라 의도적으로 짧게 잡는다.
            max_tokens=1500,
            system=SYSTEM.format(solution=report.solution),
            output_config={"effort": effort},
            messages=[{"role": "user", "content": for_llm(report)}],
        )
        if resp.stop_reason == "refusal":
            log.warning("모델이 응답을 거부했습니다: %s", resp.stop_details)
            return None
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or None

    except TypeError as exc:
        # 자격증명이 아예 없으면 SDK 가 APIError 가 아니라 TypeError 를 던진다.
        # 매일 도는 작업이라 이 경우는 traceback 없이 한 줄로 남긴다.
        if "authentication" not in str(exc).lower():
            log.exception("요약 중 예상 못 한 TypeError")
        else:
            log.warning("ANTHROPIC_API_KEY 가 설정되지 않아 요약을 건너뜁니다")
    except anthropic.AuthenticationError:
        log.warning("ANTHROPIC_API_KEY 가 유효하지 않아 요약을 건너뜁니다")
    except anthropic.RateLimitError:
        log.warning("레이트 리밋으로 요약을 건너뜁니다")
    except anthropic.APIStatusError as exc:
        log.warning("요약 실패 (HTTP %s): %s", exc.status_code, exc.message)
    except anthropic.APIConnectionError:
        log.warning("네트워크 문제로 요약을 건너뜁니다")
    except Exception:  # 요약 때문에 알림 전체가 죽는 일은 없어야 한다
        log.exception("요약 중 예상 못 한 오류")
    return None
