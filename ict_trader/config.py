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
BACKTEST_RESULTS_DIR = BASE_DIR / "backtest_results"
LOG_DIR.mkdir(exist_ok=True)
BACKTEST_RESULTS_DIR.mkdir(exist_ok=True)

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

    if GATE_API_KEY and GATE_API_SECRET:
        return

    print("\n=== Gate.io API 키 설정 ===")
    print("(실거래에 필요합니다. PAPER_TRADING=True이면 건너뛰어도 됩니다)")
    print()

    if not GATE_API_KEY:
        GATE_API_KEY = input("GATE_API_KEY: ").strip()
    if not GATE_API_SECRET:
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
UNIVERSE_TOP_N = 20                  # 시총 상위 N개
UNIVERSE_CACHE_TTL = 3600            # 1시간 캐시 (초)
UNIVERSE_EXCLUDE_STABLECOINS = True  # 스테이블코인 제외

# ──────────────────────────────────────────────
# 세션 (UTC 시간)
# ──────────────────────────────────────────────
SESSIONS = {
    "asia":     {"start": 0,  "end": 2},   # 00:00 ~ 02:00 UTC
    "london":   {"start": 7,  "end": 9},   # 07:00 ~ 09:00 UTC
    "new_york": {"start": 13, "end": 15},   # 13:00 ~ 15:00 UTC
}

# ──────────────────────────────────────────────
# 루프 설정
# ──────────────────────────────────────────────
LOOP_INTERVAL_SECONDS = 60           # 메인 루프 간격 (초)
LLM_TIMEOUT_SECONDS = 120            # LLM 응답 타임아웃 (초)
LLM_MAX_CONCURRENT = 3               # 동시 LLM 호출 최대 수
ECON_LOCK_MINUTES = 30               # 경제지표 전후 잠금 시간 (분)
SIGNAL_COOLDOWN_SECONDS = 3600       # 심볼당 마지막 신호 후 대기 시간 (초)
LLM_WAIT_MAX_CONSECUTIVE = 3         # LLM WAIT 연속 최대 횟수
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
LEVERAGE = 10                        # 고정 레버리지
USE_HALF_KELLY = True                # 하프켈리 적용 여부
KELLY_CAP = 0.20                     # 켈리 상한 (20%)
KELLY_FLOOR = 0.01                   # 켈리 하한 (1%)
MAX_MARGIN_USAGE = 0.50              # 전체 증거금 사용 한도 (50%)

# 점수 구간별 승률 추정값 (켈리 공식용)
# 백테스트 결과 기반 보정 (2026-04-12, default+default 32일)
KELLY_WIN_RATE = {
    "60-69": 0.45,       # 실측 47.7%, 보수적 적용
    "70-79": 0.40,       # 실측 36.7%, 보수적 적용 (기존 0.52 → 하향)
    "80-89": 0.45,       # 표본 소량(3건), 보수적 적용 (기존 0.60 → 하향)
    "90+":   0.50,       # 실측 데이터 없음, 보수적 적용 (기존 0.70 → 하향)
}

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
        "min_setup_score": 75,        # 진입 최소 점수
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
        "min_setup_score": 65,
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
        "min_setup_score": 55,
        "rr_min_default": 1.5,
        "rr_min_with_poi": 1.2,
        "rr_min_strong": 1.0,
        "rr_max": 10.0,
        "sl_max_risk_pct": 0.025,
    },
}

ACTIVE_PARAM_SET = "default"

# ──────────────────────────────────────────────
# 점수 가중치 세트
# ──────────────────────────────────────────────
SCORE_SETS = {
    # 보수적: HTF 중심
    "conservative": {
        # HTF (4H) 컨플루언스
        "htf_trend_aligned": 15,          # HTF 추세 방향 일치
        "htf_ob_active": 12,              # HTF OB Active POI
        "htf_fvg_active": 10,             # HTF FVG Active POI
        "htf_liquidity_sweep": 10,        # HTF BSL/SSL Sweep
        "htf_pdhl_sweep": 8,              # PDH/PDL Sweep

        # MTF (15M) 컨플루언스
        "mtf_bos_choch": 8,               # MTF BOS/CHoCH
        "mtf_ob_active": 6,               # MTF OB Active
        "mtf_fvg_active": 5,              # MTF FVG Active
        "mtf_liquidity_sweep": 5,         # MTF BSL/SSL Sweep
        "mtf_htf_poi_overlap": 10,        # HTF+MTF POI 겹침 (최강)

        # LTF (5M) 컨플루언스
        "ltf_fvg_ce": 5,                  # FVG CE 진입
        "ltf_fvg_fill": 4,               # FVG Fill 진입
        "ltf_iofed": 5,                  # IOFED 진입
        "ltf_session_sweep": 3,           # Session Range Sweep
        "ltf_liquidity_sweep": 3,         # LTF BSL/SSL Sweep
        "ltf_bos_choch": 3,              # LTF BOS/CHoCH

        # R:R 보너스
        "rr_bonus_threshold": 3.0,        # 이 R:R 이상이면 보너스
        "rr_bonus_points": 5,             # R:R 보너스 점수
    },

    # 기본
    "default": {
        "htf_trend_aligned": 12,
        "htf_ob_active": 10,
        "htf_fvg_active": 8,
        "htf_liquidity_sweep": 8,
        "htf_pdhl_sweep": 6,

        "mtf_bos_choch": 10,
        "mtf_ob_active": 8,
        "mtf_fvg_active": 7,
        "mtf_liquidity_sweep": 6,
        "mtf_htf_poi_overlap": 12,

        "ltf_fvg_ce": 7,
        "ltf_fvg_fill": 6,
        "ltf_iofed": 7,
        "ltf_session_sweep": 4,
        "ltf_liquidity_sweep": 4,
        "ltf_bos_choch": 4,

        "rr_bonus_threshold": 2.5,
        "rr_bonus_points": 5,
    },

    # 공격적: LTF 중심
    "aggressive": {
        "htf_trend_aligned": 8,
        "htf_ob_active": 7,
        "htf_fvg_active": 6,
        "htf_liquidity_sweep": 5,
        "htf_pdhl_sweep": 4,

        "mtf_bos_choch": 10,
        "mtf_ob_active": 8,
        "mtf_fvg_active": 7,
        "mtf_liquidity_sweep": 7,
        "mtf_htf_poi_overlap": 10,

        "ltf_fvg_ce": 10,
        "ltf_fvg_fill": 9,
        "ltf_iofed": 10,
        "ltf_session_sweep": 6,
        "ltf_liquidity_sweep": 6,
        "ltf_bos_choch": 6,

        "rr_bonus_threshold": 2.0,
        "rr_bonus_points": 5,
    },
}

ACTIVE_SCORE_SET = "default"

# ──────────────────────────────────────────────
# 뉴스 / 경제 캘린더
# ──────────────────────────────────────────────
NEWS_CACHE_TTL = 300                 # 심볼당 뉴스 캐시 (5분, 초)
NEWS_MAX_ITEMS = 5                   # LLM에 전달할 뉴스 최대 수
ECON_CALENDAR_HIGH_IMPACT_ONLY = True

# ──────────────────────────────────────────────
# 백테스트 설정
# ──────────────────────────────────────────────
BACKTEST_DAYS = 365                  # 테스트 기간 (1년)
BACKTEST_PARAM_SETS = ["conservative", "default", "aggressive"]
BACKTEST_SCORE_SETS = ["conservative", "default", "aggressive"]

# ──────────────────────────────────────────────
# 실거래 모드
# ──────────────────────────────────────────────
PAPER_TRADING = True                 # True: 모의 거래 / False: 실제 주문

# ──────────────────────────────────────────────
# LLM 설정 (Claude CLI 사용 — Max 구독 로그인 필요)
# ──────────────────────────────────────────────
LLM_CLI_MODEL = "sonnet"             # claude -p --model sonnet

# ──────────────────────────────────────────────
# 헬퍼: 현재 활성 파라미터/점수 세트 반환
# ──────────────────────────────────────────────
def get_active_params() -> dict:
    """현재 활성 알고리즘 파라미터 세트를 반환한다."""
    return PARAM_SETS[ACTIVE_PARAM_SET]


def get_active_scores() -> dict:
    """현재 활성 점수 가중치 세트를 반환한다."""
    return SCORE_SETS[ACTIVE_SCORE_SET]


def get_kelly_win_rate(score: float) -> float:
    """점수에 해당하는 켈리 승률 추정값을 반환한다."""
    if score >= 90:
        return KELLY_WIN_RATE["90+"]
    elif score >= 80:
        return KELLY_WIN_RATE["80-89"]
    elif score >= 70:
        return KELLY_WIN_RATE["70-79"]
    elif score >= 60:
        return KELLY_WIN_RATE["60-69"]
    return 0.0
