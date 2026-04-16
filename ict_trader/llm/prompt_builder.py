"""
LLM 프롬프트 빌더.
알고리즘 신호 + 차트 이미지 + 뉴스 + 경제지표를 Claude 프롬프트로 구성.
"""

from __future__ import annotations

import logging

from ict_trader.algorithm.trigger import TriggerEvent, Confluence

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 ICT(Inner Circle Trader) 전략 기반 암호화폐 트레이딩 분석가입니다.

역할:
- 알고리즘이 생성한 트레이딩 신호를 검토합니다.
- 방향, 진입가, SL, TP는 알고리즘이 이미 결정했습니다.
- 당신은 PASS 또는 WAIT만 판단합니다.

판단 기준:
1. ICT 구조(OB, FVG, Swing, BOS/CHoCH)가 알고리즘 분석과 일치하는지
2. 뉴스가 포지션 방향에 역행하지 않는지
3. 고영향 경제지표가 임박하여 변동성 리스크가 있는지
4. 전반적인 시장 컨텍스트가 진입에 적합한지

PASS: 신호가 합리적이고 리스크가 수용 가능 → 즉시 진입
WAIT: 지금은 진입하면 안 됨 → 일정 시간 후 재검토

반드시 아래 JSON 형식으로만 응답하세요. JSON 외 다른 텍스트를 포함하지 마세요.
```json
{
  "verdict": "PASS | WAIT",
  "reasoning": "3줄 이내 한국어 설명",
  "news_impact": "POSITIVE | NEGATIVE | NEUTRAL",
  "econ_risk": "HIGH | LOW",
  "wait_minutes": "WAIT일 때 재검토까지 대기 시간 (5~120분 정수), PASS면 null"
}
```"""


def build_signal_text(trigger: TriggerEvent) -> str:
    """TriggerEvent를 텍스트 요약으로 변환."""
    direction_kr = "롱 (매수)" if trigger.direction == "bullish" else "숏 (매도)"

    lines = [
        f"## 트레이딩 신호",
        f"- 심볼: {trigger.symbol}",
        f"- 방향: {direction_kr}",
        f"- 진입가: ${trigger.entry_price:,.2f}",
        f"- 손절가(SL): ${trigger.stop_loss:,.2f}",
        f"- 목표가(TP): ${trigger.take_profit:,.2f}",
        f"- R:R 비율: 1:{trigger.rr_ratio:.1f}",
        f"- 컨플루언스: {len(trigger.confluences)}개",
        f"- 진입 방식: {trigger.entry_type}",
        f"- 세션: {trigger.session}",
        f"- HTF 추세: {trigger.htf_trend}",
        "",
        "### 컨플루언스 항목",
    ]

    for conf in trigger.confluences:
        detail = f" ({conf.detail})" if conf.detail else ""
        lines.append(f"- {conf.name}{detail}")

    return "\n".join(lines)


def build_news_text(news_list: list[dict]) -> str:
    """뉴스 리스트를 텍스트로 변환."""
    if not news_list:
        return "## 관련 뉴스\n- 최근 주요 뉴스 없음"

    lines = ["## 관련 뉴스"]
    for i, news in enumerate(news_list, 1):
        lines.append(f"{i}. [{news.get('source', '?')}] {news.get('title', '제목 없음')}")
        if news.get("published_at"):
            lines.append(f"   발행: {news['published_at'][:16]}")

    return "\n".join(lines)


def build_econ_text(events: list[dict]) -> str:
    """경제지표 이벤트를 텍스트로 변환."""
    if not events:
        return "## 경제지표\n- 향후 4시간 내 고영향 이벤트 없음"

    lines = ["## 경제지표 (향후 고영향 이벤트)"]
    for ev in events:
        time_str = ""
        if ev.get("time_utc"):
            time_str = ev["time_utc"].strftime("%H:%M UTC")
        lines.append(f"- [{ev.get('currency', '?')}] {ev.get('event', '?')} @ {time_str}")

    return "\n".join(lines)


def build_prompt(
    trigger: TriggerEvent,
    news_list: list[dict],
    econ_events: list[dict],
) -> str:
    """
    LLM에 전달할 텍스트 프롬프트를 조합한다.
    차트 이미지는 별도 content block으로 전달하므로 여기선 텍스트만.

    Args:
        trigger: 알고리즘 트리거 이벤트
        news_list: CryptoPanic 뉴스 리스트
        econ_events: 고영향 경제지표 이벤트

    Returns:
        조합된 프롬프트 텍스트
    """
    parts = [
        build_signal_text(trigger),
        "",
        build_news_text(news_list),
        "",
        build_econ_text(econ_events),
        "",
        "---",
        "위 정보와 첨부된 4H/5M 차트 이미지를 검토하고, JSON 형식으로 판단을 내려주세요.",
    ]

    return "\n".join(parts)


def build_message_content(
    prompt_text: str,
    chart_base64: str | None = None,
) -> list[dict]:
    """
    Claude API messages 형식의 content 블록을 생성한다.
    텍스트 + (선택) 이미지.

    Args:
        prompt_text: 텍스트 프롬프트
        chart_base64: 차트 이미지 base64 (없으면 텍스트만)

    Returns:
        content 블록 리스트
    """
    content: list[dict] = []

    if chart_base64:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": chart_base64,
            },
        })

    content.append({
        "type": "text",
        "text": prompt_text,
    })

    return content
