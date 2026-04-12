"""
STEP 14: PAPER_TRADING 시범 실행 (1회, 세션 체크 무시).
전체 파이프라인을 한 번 돌려서 정상 동작 확인.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ict_trader.config import PAPER_TRADING, LOG_DIR, get_active_params
from ict_trader.data.universe import fetch_top_coins
from ict_trader.data.fetcher import fetch_multi_timeframe, close_exchange
from ict_trader.data.news import fetch_news
from ict_trader.data.economic_calendar import get_upcoming_events
from ict_trader.algorithm.market_structure import analyze as ms_analyze
from ict_trader.algorithm.trigger import analyze_topdown
from ict_trader.chart.visualizer import generate_chart_for_trigger
from ict_trader.llm.analyzer import analyze_signal
from ict_trader.execution.gate_executor import calculate_kelly_fraction, calculate_position_size
from ict_trader.execution.notifier import notify_signal, notify_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("ccxt").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def run_step14():
    print("=" * 60)
    print(f"  STEP 14: 시범 실행 (PAPER_TRADING={PAPER_TRADING})")
    print("  세션 체크 OFF — 전체 파이프라인 1회 테스트")
    print("=" * 60)
    print()

    # 1. 유니버스
    print("[1/7] 유니버스 조회...")
    symbols = fetch_top_coins()
    if not symbols:
        print("  FAIL — 유니버스 비어있음")
        return
    print(f"  OK — {len(symbols)}개: {', '.join(symbols[:5])}...")
    print()

    # 2. 상위 3개 심볼만 테스트
    test_symbols = symbols[:3]
    print(f"[2/7] 테스트 심볼: {test_symbols}")
    print()

    # 3. 데이터 수집 + 알고리즘 분석
    triggers = []
    for symbol in test_symbols:
        print(f"[3/7] {symbol} 데이터 수집 + 분석...")
        try:
            frames = await fetch_multi_timeframe(symbol)
            htf_df = frames.get("4h")
            mtf_df = frames.get("15m")
            ltf_df = frames.get("5m")

            if htf_df is None or htf_df.empty:
                print(f"  {symbol}: HTF 데이터 없음, 스킵")
                continue
            if mtf_df is None or mtf_df.empty:
                print(f"  {symbol}: MTF 데이터 없음, 스킵")
                continue
            if ltf_df is None or ltf_df.empty:
                print(f"  {symbol}: LTF 데이터 없음, 스킵")
                continue

            print(f"  데이터: 4h={len(htf_df)}봉, 15m={len(mtf_df)}봉, 5m={len(ltf_df)}봉")

            # 탑다운 분석 (세션 = 강제 뉴욕)
            trigger = analyze_topdown(symbol, htf_df, mtf_df, ltf_df, "new_york")

            if trigger:
                print(f"  >>> 신호 감지! {trigger.direction} | 점수={trigger.setup_score}({trigger.grade}) | R:R=1:{trigger.rr_ratio:.1f}")
                triggers.append((trigger, htf_df, ltf_df))
            else:
                print(f"  신호 없음 (조건 미충족)")

        except Exception as e:
            print(f"  {symbol} 오류: {e}")
        print()

    # 4. 신호가 있으면 차트 생성
    if not triggers:
        print("[4/7] 신호 없음 — 나머지 심볼도 시도합니다...")
        print()
        # 추가 심볼 시도
        for symbol in symbols[3:8]:
            print(f"  {symbol} 분석 중...")
            try:
                frames = await fetch_multi_timeframe(symbol)
                htf_df, mtf_df, ltf_df = frames.get("4h"), frames.get("15m"), frames.get("5m")
                if htf_df is None or htf_df.empty or mtf_df is None or mtf_df.empty or ltf_df is None or ltf_df.empty:
                    continue
                trigger = analyze_topdown(symbol, htf_df, mtf_df, ltf_df, "new_york")
                if trigger:
                    print(f"  >>> 신호 감지! {trigger.direction} | 점수={trigger.setup_score}({trigger.grade})")
                    triggers.append((trigger, htf_df, ltf_df))
                    break
            except Exception as e:
                print(f"  {symbol} 오류: {e}")
        print()

    if not triggers:
        print("=" * 60)
        print("  모든 심볼에서 신호 없음.")
        print("  이는 정상입니다 — 알고리즘 조건이 까다로움.")
        print("  파이프라인 동작 확인: 데이터수집 → 분석 → OK")
        print()
        print("  실거래 시 main.py가 60초마다 반복 스캔합니다.")
        print("=" * 60)
        await close_exchange()
        return

    # 최고 점수 신호 선택
    trigger, htf_df, ltf_df = max(triggers, key=lambda t: t[0].setup_score)
    print(f"[5/7] 최고 점수 신호: {trigger.symbol} {trigger.direction}")
    print(f"  진입=${trigger.entry_price:,.2f} SL=${trigger.stop_loss:,.2f} TP=${trigger.take_profit:,.2f}")
    print()

    # 차트 생성
    print("[5/7] 차트 생성...")
    try:
        htf_ms = ms_analyze(htf_df, 5)
        ltf_ms = ms_analyze(ltf_df, 3)
        chart_b64, chart_bytes = generate_chart_for_trigger(trigger, htf_df, ltf_df, htf_ms, ltf_ms)
        print(f"  OK — 차트 {len(chart_bytes):,} bytes")
    except Exception as e:
        print(f"  차트 생성 실패 (계속 진행): {e}")
        chart_b64, chart_bytes = None, None
    print()

    # 뉴스 + 경제지표
    print("[6/7] 뉴스 + 경제지표 수집...")
    news_list = fetch_news(trigger.symbol)
    econ_events = get_upcoming_events(hours_ahead=4)
    print(f"  뉴스 {len(news_list)}건, 경제지표 {len(econ_events)}건")
    print()

    # LLM 검토
    print("[7/7] Claude CLI로 LLM 검토 요청...")
    print("  (claude -p 호출 중, 잠시 대기...)")
    try:
        verdict = await analyze_signal(trigger, chart_b64, news_list, econ_events)
        print(f"  판단: {verdict.verdict}")
        print(f"  사유: {verdict.reasoning}")
        print(f"  뉴스: {verdict.news_impact} | 경제: {verdict.econ_risk}")

        if verdict.verdict == "PASS":
            kelly = calculate_kelly_fraction(trigger.setup_score, trigger.rr_ratio)
            margin, amount = calculate_position_size(84.96, kelly, trigger.entry_price, 0.0)
            print(f"\n  [PAPER] 켈리={kelly*100:.1f}%, 증거금=${margin:.2f}, 수량={amount:.6f}")
            print("  (PAPER_TRADING=True → 실제 주문 안 함)")
    except Exception as e:
        print(f"  LLM 오류: {e}")
        print("  (claude CLI가 터미널에 로그인되어 있는지 확인하세요)")
    print()

    print("=" * 60)
    print("  STEP 14 완료!")
    print("  전체 파이프라인: 데이터 → 분석 → 차트 → LLM → 사이징")
    print()
    print("  실거래 준비가 되면:")
    print("  config.py에서 PAPER_TRADING = False로 변경")
    print("=" * 60)

    await close_exchange()


if __name__ == "__main__":
    asyncio.run(run_step14())
