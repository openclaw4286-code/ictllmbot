"""
STEP 13: Gate.io API 키 연결 및 잔액 확인.
API 키가 정상 동작하는지, 잔액 조회가 되는지 테스트.
"""

from __future__ import annotations

import asyncio
import sys
import os

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ict_trader.config import prompt_api_keys, GATE_API_KEY, GATE_API_SECRET, PAPER_TRADING
from ict_trader.data.fetcher import get_exchange, close_exchange, fetch_ohlcv


async def run_step13():
    print("=" * 60)
    print("  STEP 13: Gate.io API 연결 테스트")
    print("=" * 60)
    print()

    # 1. API 키 확인/입력
    prompt_api_keys()

    from ict_trader.config import GATE_API_KEY, GATE_API_SECRET
    if not GATE_API_KEY or not GATE_API_SECRET:
        print("[SKIP] API 키 미입력 — PAPER_TRADING 모드에서는 불필요합니다.")
        print("       실거래 시에는 키를 입력해야 합니다.")
        print()

    # 2. 거래소 연결
    print("[1/4] 거래소 연결 중...")
    try:
        exchange = await get_exchange()
        await exchange.load_markets()
        print(f"  OK — Gate.io 연결 성공 (마켓 {len(exchange.markets)}개)")
    except Exception as e:
        print(f"  FAIL — 거래소 연결 실패: {e}")
        await close_exchange()
        return
    print()

    # 3. 잔액 조회
    print("[2/4] USDT 잔액 조회 중...")
    try:
        balance = await exchange.fetch_balance()
        usdt = balance.get("USDT", {})
        free = float(usdt.get("free", 0))
        used = float(usdt.get("used", 0))
        total = float(usdt.get("total", 0))
        print(f"  OK — USDT 잔액: ${total:,.2f} (사용가능: ${free:,.2f}, 사용중: ${used:,.2f})")
    except Exception as e:
        print(f"  FAIL — 잔액 조회 실패: {e}")
        print("         API 키 권한을 확인하세요 (읽기 권한 필요)")
    print()

    # 4. OHLCV 수집 테스트
    print("[3/4] BTC/USDT OHLCV 수집 테스트 (5m, 10봉)...")
    try:
        df = await fetch_ohlcv("BTC/USDT", "5m", 10)
        if not df.empty:
            last = df.iloc[-1]
            print(f"  OK — 최근 가격: ${last['close']:,.2f} ({df.index[-1]})")
            print(f"       수신 {len(df)}봉")
        else:
            print("  WARN — 데이터 비어있음")
    except Exception as e:
        print(f"  FAIL — OHLCV 수집 실패: {e}")
    print()

    # 5. tick_size 확인
    print("[4/4] BTC/USDT tick_size 확인...")
    try:
        from ict_trader.data.fetcher import get_tick_size
        info = await get_tick_size("BTC/USDT")
        print(f"  OK — price_precision: {info['price_precision']}")
        print(f"       amount_precision: {info['amount_precision']}")
        print(f"       min_amount: {info['min_amount']}")
    except Exception as e:
        print(f"  FAIL — tick_size 조회 실패: {e}")
    print()

    # 결과 요약
    print("=" * 60)
    print(f"  PAPER_TRADING: {PAPER_TRADING}")
    print(f"  API 키 설정: {'OK' if GATE_API_KEY else 'SKIP'}")
    print()
    if PAPER_TRADING:
        print("  다음 단계: python3 -m ict_trader.main")
        print("  (PAPER_TRADING=True 상태로 시범 실행)")
    else:
        print("  실거래 모드입니다. 주의하세요!")
    print("=" * 60)

    await close_exchange()


if __name__ == "__main__":
    asyncio.run(run_step13())
