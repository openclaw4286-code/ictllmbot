"""
LLM 분석기.
Claude API를 호출하여 PASS/REJECT/WAIT 판단을 받는다.
비동기, 타임아웃, 동시 호출 제한 지원.
"""

from __future__ import annotations

import json
import asyncio
import logging
from dataclasses import dataclass

import anthropic

from ict_trader.config import ANTHROPIC_API_KEY, LLM_MODEL, LLM_TIMEOUT_SECONDS, LLM_MAX_CONCURRENT
from ict_trader.llm.prompt_builder import (
    SYSTEM_PROMPT,
    build_prompt,
    build_message_content,
)
from ict_trader.algorithm.trigger import TriggerEvent

logger = logging.getLogger(__name__)

# 동시 호출 제한 세마포어
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(LLM_MAX_CONCURRENT)
    return _semaphore


@dataclass
class LLMVerdict:
    """LLM 판단 결과."""
    verdict: str            # "PASS" / "REJECT" / "WAIT"
    reasoning: str          # 한국어 3줄 이내
    news_impact: str        # "POSITIVE" / "NEGATIVE" / "NEUTRAL"
    econ_risk: str          # "HIGH" / "LOW"
    wait_reason: str | None = None  # WAIT일 때만
    raw_response: str = ""  # 원본 응답 (디버그용)
    error: str | None = None  # 에러 발생 시


def _parse_response(text: str) -> LLMVerdict:
    """
    Claude 응답에서 JSON을 파싱한다.
    코드 블록(```json...```) 또는 순수 JSON 모두 처리.
    """
    raw = text.strip()

    # 코드 블록 제거
    if "```json" in raw:
        start = raw.index("```json") + 7
        end = raw.index("```", start)
        raw = raw[start:end].strip()
    elif "```" in raw:
        start = raw.index("```") + 3
        end = raw.index("```", start)
        raw = raw[start:end].strip()

    # JSON 객체만 추출
    brace_start = raw.find("{")
    brace_end = raw.rfind("}") + 1
    if brace_start >= 0 and brace_end > brace_start:
        raw = raw[brace_start:brace_end]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("LLM 응답 JSON 파싱 실패: %s — 원본: %s", e, text[:200])
        return LLMVerdict(
            verdict="REJECT",
            reasoning="LLM 응답 파싱 실패로 안전하게 거부",
            news_impact="NEUTRAL",
            econ_risk="LOW",
            raw_response=text,
            error=f"JSON parse error: {e}",
        )

    verdict = data.get("verdict", "REJECT").upper().strip()
    if verdict not in ("PASS", "REJECT", "WAIT"):
        logger.warning("알 수 없는 verdict: '%s', REJECT로 처리", verdict)
        verdict = "REJECT"

    return LLMVerdict(
        verdict=verdict,
        reasoning=data.get("reasoning", ""),
        news_impact=data.get("news_impact", "NEUTRAL").upper(),
        econ_risk=data.get("econ_risk", "LOW").upper(),
        wait_reason=data.get("wait_reason"),
        raw_response=text,
    )


async def analyze_signal(
    trigger: TriggerEvent,
    chart_base64: str | None,
    news_list: list[dict],
    econ_events: list[dict],
) -> LLMVerdict:
    """
    단일 신호를 LLM에 검토 요청한다.

    Args:
        trigger: 알고리즘 트리거 이벤트
        chart_base64: 차트 이미지 base64 (None이면 텍스트만)
        news_list: 뉴스 리스트
        econ_events: 경제지표 이벤트

    Returns:
        LLMVerdict
    """
    sem = _get_semaphore()

    async with sem:
        prompt_text = build_prompt(trigger, news_list, econ_events)
        content = build_message_content(prompt_text, chart_base64)

        logger.info(
            "LLM 검토 요청: %s %s %s (점수=%d)",
            trigger.symbol, trigger.direction, trigger.entry_type, trigger.setup_score,
        )

        try:
            client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

            response = await asyncio.wait_for(
                client.messages.create(
                    model=LLM_MODEL,
                    max_tokens=512,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": content}],
                ),
                timeout=LLM_TIMEOUT_SECONDS,
            )

            response_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    response_text += block.text

            verdict = _parse_response(response_text)

            logger.info(
                "LLM 판단: %s %s → %s | 뉴스=%s 경제=%s",
                trigger.symbol, trigger.direction,
                verdict.verdict, verdict.news_impact, verdict.econ_risk,
            )

            return verdict

        except asyncio.TimeoutError:
            logger.error("LLM 타임아웃: %s (%ds)", trigger.symbol, LLM_TIMEOUT_SECONDS)
            return LLMVerdict(
                verdict="WAIT",
                reasoning="LLM 응답 타임아웃",
                news_impact="NEUTRAL",
                econ_risk="LOW",
                wait_reason="timeout",
                error="timeout",
            )
        except anthropic.APIError as e:
            logger.error("Claude API 오류: %s — %s", trigger.symbol, e)
            return LLMVerdict(
                verdict="REJECT",
                reasoning=f"API 오류로 안전하게 거부",
                news_impact="NEUTRAL",
                econ_risk="LOW",
                error=str(e),
            )
        except Exception as e:
            logger.error("LLM 분석 예외: %s — %s", trigger.symbol, e)
            return LLMVerdict(
                verdict="REJECT",
                reasoning="예기치 않은 오류로 거부",
                news_impact="NEUTRAL",
                econ_risk="LOW",
                error=str(e),
            )


async def analyze_signals_batch(
    triggers: list[tuple[TriggerEvent, str | None, list[dict], list[dict]]],
) -> list[tuple[TriggerEvent, LLMVerdict]]:
    """
    여러 신호를 비동기로 동시 검토한다.
    세마포어로 LLM_MAX_CONCURRENT 제한.

    Args:
        triggers: [(trigger, chart_base64, news_list, econ_events), ...]

    Returns:
        [(trigger, verdict), ...]
    """
    tasks = [
        analyze_signal(trigger, chart_b64, news, econ)
        for trigger, chart_b64, news, econ in triggers
    ]

    verdicts = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[tuple[TriggerEvent, LLMVerdict]] = []
    for i, (trigger, _, _, _) in enumerate(triggers):
        v = verdicts[i]
        if isinstance(v, Exception):
            logger.error("배치 분석 예외: %s — %s", trigger.symbol, v)
            v = LLMVerdict(
                verdict="REJECT",
                reasoning="배치 처리 중 예외 발생",
                news_impact="NEUTRAL",
                econ_risk="LOW",
                error=str(v),
            )
        results.append((trigger, v))

    return results
