"""
LLM 분석기.
Claude CLI (claude -p)를 subprocess로 호출하여 PASS/REJECT/WAIT 판단을 받는다.
Claude Max 구독 로그인 상태에서 동작. API 키 불필요.
"""

from __future__ import annotations

import json
import asyncio
import logging
import tempfile
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ict_trader.config import LLM_TIMEOUT_SECONDS, LLM_MAX_CONCURRENT, LLM_CLI_MODEL, BASE_DIR
from ict_trader.llm.prompt_builder import (
    SYSTEM_PROMPT,
    build_prompt,
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


# LLM 판단 이력 저장
_LLM_LOG_FILE = BASE_DIR / "llm_decisions.json"


def _save_decision(trigger: "TriggerEvent", verdict: LLMVerdict) -> None:
    """LLM 판단을 JSON 파일에 append 저장."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": trigger.symbol,
        "direction": trigger.direction,
        "entry_type": trigger.entry_type,
        "entry_price": trigger.entry_price,
        "stop_loss": trigger.stop_loss,
        "take_profit": trigger.take_profit,
        "rr_ratio": trigger.rr_ratio,
        "session": trigger.session,
        "confluences_count": len(trigger.confluences),
        "confluences": [c.name for c in trigger.confluences],
        "verdict": verdict.verdict,
        "reasoning": verdict.reasoning,
        "news_impact": verdict.news_impact,
        "econ_risk": verdict.econ_risk,
        "wait_reason": verdict.wait_reason,
        "error": verdict.error,
    }

    # 기존 파일 읽기 + append
    existing: list = []
    if _LLM_LOG_FILE.exists():
        try:
            with open(_LLM_LOG_FILE, "r") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    existing.append(entry)

    try:
        with open(_LLM_LOG_FILE, "w") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("LLM 판단 저장 실패: %s", e)


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


async def _call_claude_cli(
    prompt_text: str,
    system_prompt: str,
) -> str:
    """
    Claude CLI를 subprocess로 호출한다.
    Claude Max 로그인 상태에서 'claude -p' 사용.
    shell=True로 실행하여 PATH 환경 상속.
    """
    full_prompt = f"{system_prompt}\n\n---\n\n{prompt_text}"

    cmd = f"claude -p --model {LLM_CLI_MODEL}"

    # .env의 ANTHROPIC_API_KEY 플레이스홀더가 Claude CLI를 방해하지 않도록 제거
    import os
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(full_prompt.encode("utf-8")),
        timeout=LLM_TIMEOUT_SECONDS,
    )

    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        logger.error("Claude CLI stderr: %s", stderr_text)
        logger.error("Claude CLI stdout: %s", stdout_text[:200])
        raise RuntimeError(
            f"Claude CLI 오류 (code={proc.returncode}): "
            f"{stderr_text or stdout_text or '알 수 없는 오류'}"
        )

    return stdout.decode("utf-8", errors="replace").strip()


async def analyze_signal(
    trigger: TriggerEvent,
    chart_base64: str | None,
    news_list: list[dict],
    econ_events: list[dict],
) -> LLMVerdict:
    """
    단일 신호를 Claude CLI로 검토 요청한다.

    Args:
        trigger: 알고리즘 트리거 이벤트
        chart_base64: 차트 이미지 base64 (현재 CLI 모드에서는 미사용)
        news_list: 뉴스 리스트
        econ_events: 경제지표 이벤트

    Returns:
        LLMVerdict
    """
    sem = _get_semaphore()

    async with sem:
        prompt_text = build_prompt(trigger, news_list, econ_events)

        logger.info(
            "LLM 검토 요청 (Claude CLI): %s %s %s (점수=%d)",
            trigger.symbol, trigger.direction, trigger.entry_type, trigger.rr_ratio,
        )

        try:
            response_text = await _call_claude_cli(prompt_text, SYSTEM_PROMPT)
            verdict = _parse_response(response_text)

            logger.info(
                "LLM 판단: %s %s → %s | 뉴스=%s 경제=%s",
                trigger.symbol, trigger.direction,
                verdict.verdict, verdict.news_impact, verdict.econ_risk,
            )
            _save_decision(trigger, verdict)
            return verdict

        except asyncio.TimeoutError:
            logger.error("LLM 타임아웃: %s (%ds)", trigger.symbol, LLM_TIMEOUT_SECONDS)
            verdict = LLMVerdict(
                verdict="WAIT",
                reasoning="LLM 응답 타임아웃",
                news_impact="NEUTRAL",
                econ_risk="LOW",
                wait_reason="timeout",
                error="timeout",
            )
            _save_decision(trigger, verdict)
            return verdict
        except Exception as e:
            logger.error("Claude CLI 오류: %s — %s", trigger.symbol, e)
            verdict = LLMVerdict(
                verdict="REJECT",
                reasoning="CLI 오류로 안전하게 거부",
                news_impact="NEUTRAL",
                econ_risk="LOW",
                error=str(e),
            )
            _save_decision(trigger, verdict)
            return verdict


async def analyze_signals_batch(
    triggers: list[tuple[TriggerEvent, str | None, list[dict], list[dict]]],
) -> list[tuple[TriggerEvent, LLMVerdict]]:
    """
    여러 신호를 비동기로 동시 검토한다.
    세마포어로 LLM_MAX_CONCURRENT 제한.
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
