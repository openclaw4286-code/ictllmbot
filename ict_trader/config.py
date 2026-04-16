"""
ICT 자동 트레이딩 시스템 — 중앙 설정 파일
모든 파라미터는 여기서만 관리한다. 하드코딩 절대 금지.
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# 경로
# ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────
# API 키 (.env 또는 터미널 입력)
# ──────────────────────────────────────────────
GATE_API_KEY = os.getenv("GATE_API_KEY", "")
GATE_API_SECRET = os.getenv("GATE_API_SECRET", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")


def prompt_api_keys() -> None:
    """
    Gate.io API 키가 비어있으면 터미널에서 입력받고 .env에 저장한다.
    main.py 시작 시 호출.
    """
    global GATE_API_KEY, GATE_API_SECRET
    env_path = BASE_DIR / ".env"

    def _is_valid_key(key: str) -> bool:
        return bool(key) and "your_" not in key and "here" not in key and len(key) > 10

    if _is_valid_key(GATE_API_KEY) and _is_valid_key(GATE_API_SECRET):
        print(f"Gate.io API 키: 설정됨 ({GATE_API_KEY[:6]}...)")
        return

    print("\n=== Gate.io API 키 설정 ===")
    print("(실거래에 필요합니다. PAPER_TRADING=True이면 건너뛰어도 됩니다)")
    print()

    GATE_API_KEY = input("GATE_API_KEY: ").strip()
    GATE_API_SECRET = input("GATE_API_SECRET: ").strip()

    if GATE_API_KEY and GATE_API_SECRET:
        # .env 파일에 저장
        lines = []
        if env_path.exists():
            lines = env_path.read_text().splitlines()

        # 기존 키 제거 후 추가
        lines = [l for l in lines if not l.startswith("GATE_API_KEY=") and not l.startswith("GATE_API_SECRET=")]
        lines.append(f"GATE_API_KEY={GATE_API_KEY}")
        lines.append(f"GATE_API_SECRET={GATE_API_SECRET}")
        env_path.write_text("\n".join(lines) + "\n")
        print(".env에 저장 완료!\n")
    else:
        print("키 미입력 — PAPER_TRADING 모드로 진행합니다.\n")

# ──────────────────────────────────────────────
# 거래소
# ──────────────────────────────────────────────
EXCHANGE_ID = "gateio"

# ──────────────────────────────────────────────
# 유니버스
# ──────────────────────────────────────────────
UNIVERSE_TOP_N = 30                  # 시총 상위 N개
UNIVERSE_CACHE_TTL = 3600            # 1시간 캐시 (초)
UNIVERSE_EXCLUDE_STABLECOINS = True  # 스테이블코인 제외

# ──────────────────────────────────────────────
# 세션 (UTC 시간)
# ──────────────────────────────────────────────
SESSIONS = {
    "asia":     {"start": 0,  "end": 6},    # 00:00 ~ 06:00 UTC (KST 09:00~15:00)
    "london":   {"start": 7,  "end": 12},   # 07:00 ~ 12:00 UTC (KST 16:00~21:00)
    "new_york": {"start": 12, "end": 17},   # 12:00 ~ 17:00 UTC (KST 21:00~02:00)
}

# 점심시간 제외 (각 세션 내 유동성 저하 시간대)
LUNCH_BREAKS = {
    "asia":     {"start": 3,  "end": 4},    # UTC 03~04 (KST 12:00~13:00)
    "london":   {"start": 10, "end": 11},   # UTC 10~11 (KST 19:00~20:00)
    "new_york": {"start": 15, "end": 16},   # UTC 15~16 (KST 00:00~01:00)
}

# ──────────────────────────────────────────────
# 루프 설정
# ──────────────────────────────────────────────
LOOP_INTERVAL_SECONDS = 60           # 메인 루프 간격 (초)
LLM_TIMEOUT_SECONDS = 120            # LLM 응답 타임아웃 (초)
LLM_MAX_CONCURRENT = 3               # 동시 LLM 호출 최대 수
ECON_LOCK_MINUTES = 30               # 경제지표 전후 잠금 시간 (분)
# LLM WAIT 시 대기 시간은 LLM이 동적 결정 (5~120분)
TOP_SIGNALS_FOR_LLM = 3              # LLM 검토할 상위 신호 수

# ──────────────────────────────────────────────
# 타임프레임
# ──────────────────────────────────────────────
HTF = "4h"    # High Time Frame
MTF = "15m"   # Mid Time Frame
LTF = "5m"    # Low Time Frame

# OHLCV 캔들 수
HTF_CANDLE_LIMIT = 200
MTF_CANDLE_LIMIT = 200
LTF_CANDLE_LIMIT = 200

# ──────────────────────────────────────────────
# 포지션 사이징
# ──────────────────────────────────────────────
# 동적 레버리지: SL 거리에 따라 최대 안전 레버리지 자동 계산
# L = min(LEVERAGE_MAX, 1 / (SL거리 × LIQUIDATION_SAFETY_BUFFER))
LEVERAGE_MAX = 50                    # 상한 (거래소 알트코인 한도 수준)
LEVERAGE_MIN = 3                     # 하한
LIQUIDATION_SAFETY_BUFFER = 3.0      # 청산이 SL의 3배 이상 멀리 있도록 (플래시 크래시 대응)

# ICT 표준 리스크 관리: Fixed Fractional (2% 룰)
RISK_PER_TRADE = 0.02                # 거래당 리스크 (자산의 2%, 공격적)
# 동시 포지션 수 제한 없음 — 담보금이 남아있는 한 계속 진입

# ──────────────────────────────────────────────
# 알고리즘 파라미터 세트
# ──────────────────────────────────────────────
PARAM_SETS = {
    "conservative": {
        "swing_bars": 7,              # Swing High/Low 감지 좌우 봉 수
        "fvg_ce_threshold": 0.001,    # FVG CE 허용 오차 (0.1%)
        "htf_poi_tolerance": 0.003,   # HTF Active POI 근접 허용 오차 (0.3%)
        "liquidity_lookback": 30,     # Liquidity Sweep 감지 봉 수
        "iofed_lookback": 5,          # IOFED 유효 범위 봉 수
        "rr_min_default": 2.0,        # R:R 최소 기준 (기본)
        "rr_min_with_poi": 1.8,       # R:R 최소 기준 (HTF POI 있을 때)
        "rr_min_strong": 1.5,         # R:R 최소 기준 (강한 컨플루언스)
        "rr_max": 6.0,                # R:R 최대 캡
        "sl_max_risk_pct": 0.015,     # SL 최대 허용 리스크 (1.5%)
    },
    "default": {
        "swing_bars": 5,
        "fvg_ce_threshold": 0.002,
        "htf_poi_tolerance": 0.005,
        "liquidity_lookback": 20,
        "iofed_lookback": 8,
        "rr_min_default": 1.8,
        "rr_min_with_poi": 1.5,
        "rr_min_strong": 1.2,
        "rr_max": 8.0,
        "sl_max_risk_pct": 0.02,
    },
    "aggressive": {
        "swing_bars": 3,
        "fvg_ce_threshold": 0.003,
        "htf_poi_tolerance": 0.008,
        "liquidity_lookback": 15,
        "iofed_lookback": 10,
        "rr_min_default": 1.5,
        "rr_min_with_poi": 1.2,
        "rr_min_strong": 1.0,
        "rr_max": 10.0,
        "sl_max_risk_pct": 0.025,
    },
}

ACTIVE_PARAM_SET = "default"

# ──────────────────────────────────────────────
# 뉴스 / 경제 캘린더
# ──────────────────────────────────────────────
NEWS_CACHE_TTL = 300                 # 심볼당 뉴스 캐시 (5분, 초)
NEWS_MAX_ITEMS = 5                   # LLM에 전달할 뉴스 최대 수
ECON_CALENDAR_HIGH_IMPACT_ONLY = True

# ──────────────────────────────────────────────
# 실거래 모드
# ──────────────────────────────────────────────
PAPER_TRADING = False                # True: 모의 거래 / False: 실제 주문

# ──────────────────────────────────────────────
# LLM 설정 (Claude CLI 사용 — Max 구독 로그인 필요)
# ──────────────────────────────────────────────
LLM_CLI_MODEL = "opus"               # claude -p --model opus

# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def get_active_params() -> dict:
    """현재 활성 알고리즘 파라미터 세트를 반환한다."""
    return PARAM_SETS[ACTIVE_PARAM_SET]
