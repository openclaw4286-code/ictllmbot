"""
트레이딩 세션 시간 판정.
아시아/런던/뉴욕 세션만 활성, 그 외 및 주말은 비활성.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ict_trader.config import SESSIONS, LUNCH_BREAKS

logger = logging.getLogger(__name__)


def get_current_session(now_utc: datetime | None = None) -> str | None:
    """
    현재 UTC 시간이 어떤 세션에 해당하는지 반환한다.

    Returns:
        "asia" / "london" / "new_york" / None (세션 밖)
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    # 주말 체크 (토=5, 일=6)
    if now_utc.weekday() >= 5:
        return None

    hour = now_utc.hour

    for session_name, times in SESSIONS.items():
        if times["start"] <= hour < times["end"]:
            # 점심시간 체크
            lunch = LUNCH_BREAKS.get(session_name)
            if lunch and lunch["start"] <= hour < lunch["end"]:
                return None  # 점심시간 = 비활성
            return session_name

    return None


def is_session_active(now_utc: datetime | None = None) -> bool:
    """현재 시간이 활성 세션인지 확인한다."""
    return get_current_session(now_utc) is not None


def get_session_display_name(session: str | None) -> str:
    """세션 이름을 표시용 한국어로 변환한다."""
    names = {
        "asia": "아시아",
        "london": "런던",
        "new_york": "뉴욕",
    }
    if session is None:
        return "세션 외"
    return names.get(session, session)


def get_session_range(
    session_name: str,
    date: datetime | None = None,
) -> tuple[datetime, datetime]:
    """
    특정 세션의 시작/종료 UTC datetime을 반환한다.
    백테스트에서 Session Range Sweep 감지에 사용.
    """
    if date is None:
        date = datetime.now(timezone.utc)

    times = SESSIONS[session_name]
    start = date.replace(
        hour=times["start"], minute=0, second=0, microsecond=0,
        tzinfo=timezone.utc,
    )
    end = date.replace(
        hour=times["end"], minute=0, second=0, microsecond=0,
        tzinfo=timezone.utc,
    )
    return start, end


def detect_session_range_sweep(
    df_ltf: "pd.DataFrame",
    session_name: str,
    direction: str,
) -> bool:
    """
    LTF 데이터에서 이전 세션 레인지가 스윕됐는지 감지한다.

    Bullish: 이전 세션 Low가 스윕됨 (wick이 아래로 찍고 회복)
    Bearish: 이전 세션 High가 스윕됨 (wick이 위로 찍고 회복)

    Args:
        df_ltf: LTF OHLCV DataFrame (UTC 인덱스)
        session_name: 이전 세션 이름
        direction: "bullish" or "bearish"

    Returns:
        스윕 감지 여부
    """
    import pandas as pd

    if df_ltf.empty:
        return False

    # 현재 시점 기준 가장 최근 완료된 세션의 데이터 필터링
    now = df_ltf.index[-1]
    times = SESSIONS.get(session_name)
    if not times:
        return False

    # 같은 날 또는 전날의 세션 데이터
    session_data = df_ltf[
        (df_ltf.index.hour >= times["start"]) & (df_ltf.index.hour < times["end"])
    ]

    if session_data.empty or len(session_data) < 2:
        return False

    session_high = session_data["high"].max()
    session_low = session_data["low"].min()

    # 세션 이후 데이터
    after_session = df_ltf[df_ltf.index > session_data.index[-1]]
    if after_session.empty:
        return False

    if direction == "bullish":
        # 세션 Low 스윕: wick이 세션 Low 아래로 갔다가 close는 위
        for i in range(len(after_session)):
            if (after_session["low"].iloc[i] < session_low and
                    after_session["close"].iloc[i] > session_low):
                return True
    elif direction == "bearish":
        # 세션 High 스윕: wick이 세션 High 위로 갔다가 close는 아래
        for i in range(len(after_session)):
            if (after_session["high"].iloc[i] > session_high and
                    after_session["close"].iloc[i] < session_high):
                return True

    return False
