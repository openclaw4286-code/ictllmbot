# ICT 자동 트레이딩 시스템

ICT(Inner Circle Trader) 전략 기반 암호화폐 선물 자동매매 봇.
알고리즘이 ICT 트리거를 감지하고 신호를 생성하면 LLM(Claude)이 최종 PASS/REJECT를 결정한다.

## 기술 스택

| 항목 | 사용 |
|---|---|
| 언어 | Python 3.9+ (asyncio) |
| 거래소 | Gate.io 선물 (ccxt) |
| 유니버스 | CoinGecko 시총 상위 30개 (스테이블코인 제외, 1시간 캐시) |
| 차트 | mplfinance → base64 (Claude Vision용) |
| 뉴스 | CoinDesk/CoinTelegraph RSS (5분 캐시) |
| 경제 캘린더 | 정기 이벤트 + FXStreet RSS |
| LLM | Claude CLI (`claude -p --model opus`, Max 구독) |
| 알림 | 콘솔/로그 출력 |

---

## 폴더 구조

```
ict_trader/
├── main.py                  # 메인 루프 (60초 주기)
├── config.py                # 모든 설정값 중앙 관리
├── .env                     # API 키 (gitignore)
├── trade_history.json       # 거래 승/패 이력 (자동 생성, gitignore)
├── llm_decisions.json       # LLM 판단 이력 (자동 생성, gitignore)
│
├── data/
│   ├── universe.py          # CoinGecko TOP30 + Gate.io 마켓 검증
│   ├── fetcher.py           # ccxt OHLCV 수집 (4H/15M/5M 동시)
│   ├── news.py              # RSS 뉴스 (CoinDesk/CoinTelegraph)
│   └── economic_calendar.py # 정기 경제지표 + FXStreet RSS
│
├── algorithm/
│   ├── market_structure.py  # Swing H/L, BOS/CHoCH, 추세 판단
│   ├── order_block.py       # OB 감지, Active POI 판정
│   ├── fvg.py               # FVG 감지, CE 계산, Fill 추적
│   ├── liquidity.py         # BSL/SSL Sweep 감지
│   ├── session.py           # 세션 시간 판정 (아시아/런던/뉴욕)
│   ├── pdhl.py              # PDH/PDL Sweep
│   ├── iofed.py             # IOFED 패턴 (연속봉 후 반전)
│   └── trigger.py           # 3단계 탑다운 → TriggerEvent 생성
│
├── chart/
│   └── visualizer.py        # 4H+5M 듀얼 캔들차트 (OB/FVG/SL/TP 마킹)
│
├── llm/
│   ├── prompt_builder.py    # 시스템 프롬프트 + 신호/뉴스/경제지표 텍스트
│   └── analyzer.py          # Claude CLI 호출, JSON 파싱, 판단 이력 저장
│
├── execution/
│   ├── gate_executor.py     # tick_size 정밀도, 사이징, 레버리지, 주문 실행
│   ├── position_manager.py  # 포지션 추적, 거래소 동기화, 승/패 감지
│   └── notifier.py          # 콘솔 알림
│
├── state/
│   └── loop_state.py        # 심볼별 쿨다운, LLM 플래그, 루프 통계
│
├── logs/                    # 일별 로그 파일 (gitignore)
└── requirements.txt
```

---

## 설정 (config.py)

모든 파라미터는 `config.py`에서 관리. 하드코딩 금지.

### 세션 (UTC)

| 세션 | UTC | KST |
|---|---|---|
| 아시아 | 00:00~02:00 | 09:00~11:00 |
| 런던 | 07:00~09:00 | 16:00~18:00 |
| 뉴욕 | 13:00~15:00 | 22:00~00:00 |

세션 외 시간 및 주말(토/일)은 스캔하지 않음.

### 루프 설정

| 항목 | 값 | 설명 |
|---|---|---|
| `LOOP_INTERVAL_SECONDS` | 60 | 메인 루프 간격 (초) |
| `LLM_TIMEOUT_SECONDS` | 120 | Claude CLI 응답 대기 (초) |
| `LLM_MAX_CONCURRENT` | 3 | 동시 LLM 호출 최대 수 |
| `ECON_LOCK_MINUTES` | 30 | 경제지표 전후 잠금 (분) |
| `SIGNAL_COOLDOWN_SECONDS` | 3600 | 심볼당 신호 후 대기 (초) |
| `LLM_WAIT_MAX_CONSECUTIVE` | 3 | WAIT 연속 최대 횟수 |
| `TOP_SIGNALS_FOR_LLM` | 3 | R:R 상위 N개만 LLM 검토 |

### 타임프레임

| 역할 | 타임프레임 | 캔들 수 |
|---|---|---|
| HTF (방향 판단) | 4H | 200 |
| MTF (구조 확인) | 15M | 200 |
| LTF (정밀 진입) | 5M | 200 |

### 리스크 관리 (Fixed Fractional, ICT 표준)

| 항목 | 값 | 설명 |
|---|---|---|
| `RISK_PER_TRADE` | 0.02 (2%) | 거래당 리스크 (자산 대비) |
| 동시 포지션 | 제한 없음 | 담보금 남는 한 계속 진입 |
| `LEVERAGE_MAX` | 50 | 레버리지 상한 |
| `LEVERAGE_MIN` | 3 | 레버리지 하한 |
| `LIQUIDATION_SAFETY_BUFFER` | 3.0 | 청산이 SL의 3배 이상 멀리 |

### 알고리즘 파라미터 세트

`ACTIVE_PARAM_SET = "default"` (3개 세트: conservative / default / aggressive)

| 파라미터 | conservative | **default** | aggressive |
|---|---|---|---|
| `swing_bars` | 7 | **5** | 3 |
| `fvg_ce_threshold` | 0.1% | **0.2%** | 0.3% |
| `htf_poi_tolerance` | 0.3% | **0.5%** | 0.8% |
| `liquidity_lookback` | 30봉 | **20봉** | 15봉 |
| `iofed_lookback` | 5봉 | **8봉** | 10봉 |
| `rr_min_default` | 2.0 | **1.8** | 1.5 |
| `rr_min_with_poi` | 1.8 | **1.5** | 1.2 |
| `rr_min_strong` | 1.5 | **1.2** | 1.0 |
| `rr_max` | 6.0 | **8.0** | 10.0 |
| `sl_max_risk_pct` | 1.5% | **2.0%** | 2.5% |

---

## ICT 알고리즘 — 3단계 탑다운 분석

`algorithm/trigger.py`의 `analyze_topdown()` 함수가 전체 파이프라인을 실행한다.
HTF → MTF → LTF 3단계를 순차 통과해야만 `TriggerEvent`가 생성된다.

### 전체 흐름

```
HTF(4H) 방향 판단
  ↓ neutral이면 스킵
MTF(15M) 구조 확인
  ↓ BOS/CHoCH 또는 OB/FVG 없으면 스킵
LTF(5M) 정밀 진입
  ↓ FVG CE / FVG Fill / IOFED 중 하나 없으면 스킵
SL 결정 → TP 결정 → R:R 계산
  ↓ R:R < 최소 기준이면 스킵
TriggerEvent 생성 → LLM 검토로 전달
```

---

### STEP 1: HTF (4H) — 방향 판단

**파일**: `algorithm/market_structure.py`

#### Swing High/Low 감지 (`detect_swing_points`)

좌우 `swing_bars`개 봉과 비교하여 현재 봉이 최고/최저인지 판정.

```
Swing High: bar[i].high > 좌측 swing_bars개 max AND 우측 swing_bars개 max
Swing Low:  bar[i].low  < 좌측 swing_bars개 min AND 우측 swing_bars개 min
```

| 상황 | 결과 |
|---|---|
| 데이터 < `swing_bars × 2 + 1` 봉 | 빈 결과 반환 (분석 불가) |
| Swing 3개 미만 | BOS/CHoCH 감지 불가 |

#### BOS / CHoCH 감지 (`detect_structure_breaks`)

모든 Swing Point를 시간순 정렬 후 순차 처리:

| 조건 | 결과 |
|---|---|
| HH (Higher High) + 기존 추세 bullish/neutral | **BOS (bullish)** — 추세 유지 |
| HH + 기존 추세 bearish | **CHoCH (bullish)** — 추세 반전 |
| LL (Lower Low) + 기존 추세 bearish/neutral | **BOS (bearish)** — 추세 유지 |
| LL + 기존 추세 bullish | **CHoCH (bearish)** — 추세 반전 |

#### 추세 판단 (`determine_trend`)

| 우선순위 | 조건 | 결과 |
|---|---|---|
| 1순위 | 최근 2개 Swing: HH + HL | `bullish` |
| 1순위 | 최근 2개 Swing: LH + LL | `bearish` |
| 2순위 | 불명확 → 최근 BOS/CHoCH 방향 | 해당 방향 |
| 없음 | 위 모두 불충족 | `neutral` → **스킵** |

#### HTF에서 추가로 수행하는 분석

**Order Block 감지** (`algorithm/order_block.py`):

BOS/CHoCH 발생 봉에서 역순 최대 10봉 탐색:

| 방향 | OB 캔들 | 의미 |
|---|---|---|
| Bullish OB | `close < open` (하락 캔들) | 상승 직전 매집 영역 |
| Bearish OB | `close > open` (상승 캔들) | 하락 직전 분배 영역 |

OB 활성 상태 관리:

| 조건 | 상태 |
|---|---|
| Bullish OB인데 이후 `low < OB.low` | `is_active = False` (미티게이트됨) |
| Bearish OB인데 이후 `high > OB.high` | `is_active = False` |
| 현재가와 OB 거리 ≤ `htf_poi_tolerance` | `is_poi = True` (Active POI) |

**FVG 감지** (`algorithm/fvg.py`):

3-캔들 패턴에서 1번째와 3번째 캔들 사이의 가격 갭:

| 방향 | 조건 | 갭 범위 |
|---|---|---|
| Bullish FVG | `candle[i-1].high < candle[i+1].low` | [candle[i-1].high, candle[i+1].low] |
| Bearish FVG | `candle[i-1].low > candle[i+1].high` | [candle[i+1].high, candle[i-1].low] |

FVG CE (Consequent Encroachment) = `(high + low) / 2` (갭 중간값)

FVG 채움 상태 추적:

| 상태 | 조건 |
|---|---|
| `fill_pct` | 이후 캔들이 FVG 안으로 얼마나 침투했는지 (0.0~1.0) |
| `is_filled = True` | `fill_pct ≥ 0.5` (50% 이상 채워짐) |
| `is_active = False` | 갭이 완전 관통됨 (100% 채움) |

**Liquidity Sweep 감지** (`algorithm/liquidity.py`):

| Sweep 유형 | 조건 | 방향별 의미 |
|---|---|---|
| BSL (Buy-Side) | `high > Swing High` & `close < Swing High` | Bearish 컨플루언스 |
| SSL (Sell-Side) | `low < Swing Low` & `close > Swing Low` | Bullish 컨플루언스 |

`reclaimed = True` (close가 레벨 안으로 되돌아왔을 때)만 유효한 Sweep.
최근 `liquidity_lookback`봉 내에서만 탐색. Swing당 첫 Sweep만 기록.

**PDH/PDL Sweep** (`algorithm/pdhl.py`):

| 방향 | 조건 |
|---|---|
| Bullish 보강 | 당일 `low < PDL` & `close > PDL` (전일 저가 스윕 후 회복) |
| Bearish 보강 | 당일 `high > PDH` & `close < PDH` (전일 고가 스윕 후 회복) |

PDH/PDL은 UTC 기준 전일 고가/저가. 최소 2일치 데이터 필요.

---

### STEP 2: MTF (15M) — 구조 확인

HTF와 동일한 Market Structure 분석을 15M 데이터로 수행.

**통과 조건** (둘 중 하나 이상 충족):

| 조건 | 설명 |
|---|---|
| MTF BOS/CHoCH | HTF 방향과 일치하는 구조 브레이크 존재 |
| MTF OB 또는 FVG | 방향 맞는 활성 OB 또는 FVG 존재 |

**둘 다 없으면 → 스킵**

추가 분석:
- MTF Liquidity Sweep (방향 필터링)
- **HTF+MTF POI 겹침**: HTF OB와 MTF OB (또는 FVG끼리)의 가격 범위가 겹치면 최강 컨플루언스

---

### STEP 3: LTF (5M) — 정밀 진입

**진입 조건 3가지 중 하나** (순서대로 탐색, 먼저 발견된 것 사용):

#### 1. FVG CE 진입

| 항목 | 설명 |
|---|---|
| 조건 | 현재가가 활성 FVG의 CE(중간값)에서 `fvg_ce_threshold` 이내 |
| 진입가 | FVG CE 값 |
| 탐색 방향 | 최근 FVG부터 역순 |

#### 2. FVG Fill 진입

| 항목 | 설명 |
|---|---|
| 조건 | 활성 FVG가 50% 이상 채워졌지만 아직 완전 관통되지 않음 |
| 진입가 | 현재가 |

#### 3. IOFED 진입 (`algorithm/iofed.py`)

| 항목 | 설명 |
|---|---|
| 조건 | 연속 하락봉(또는 상승봉) 3개+ 후 반전 캔들 |
| 탐색 범위 | 최근 `iofed_lookback`봉 이내 |
| 진입가 | 반전 캔들의 close |

| 방향 | IOFED 패턴 |
|---|---|
| Bullish | 3개+ 연속 `close < open` → 1개 `close > open` (반전) |
| Bearish | 3개+ 연속 `close > open` → 1개 `close < open` (반전) |

**3가지 모두 미충족 시 → 스킵**

#### LTF 추가 컨플루언스

| 항목 | 설명 |
|---|---|
| Session Range Sweep | 이전 세션(아시아→뉴욕→런던 순환) 레인지 스윕 감지 |
| LTF Liquidity Sweep | LTF 봉에서 BSL/SSL Sweep |
| LTF BOS/CHoCH | LTF 구조 브레이크 (방향 일치) |

---

### SL 결정

**우선순위** (위에서 아래로, 유효한 것 중 진입가에 가장 가까운 레벨):

| 순위 | SL 후보 | 설명 |
|---|---|---|
| 1 | HTF OB low/high | 가장 강한 구조적 레벨 |
| 2 | MTF OB low/high | 차선 |
| 3 | LTF 최근 Swing Low/High | 가장 가까운 변곡점 |
| 4 | PDH/PDL | 전일 고/저 |
| 5 | **폴백**: entry × (1 ± `sl_max_risk_pct`) | 구조 레벨 없을 때 고정 % |

**유효성 검증**:
- `|entry - SL| / entry ≤ sl_max_risk_pct` (최대 리스크 % 이내)
- 방향 맞는지 (Bullish: SL < entry, Bearish: SL > entry)
- 유효 후보 중 진입가에 **가장 가까운** 것 선택 (타이트한 SL = 높은 R:R 가능)

---

### TP 결정

| 단계 | 설명 |
|---|---|
| 1 | HTF + MTF의 Swing High/Low에서 **반대편 유동성 타겟** 수집 |
| 2 | Bullish → Swing High들 (가까운 순), Bearish → Swing Low들 |
| 3 | 각 타겟의 R:R = `|target - entry| / |entry - SL|` 계산 |
| 4 | `rr_min ≤ R:R ≤ rr_max` 충족하는 **첫 번째 타겟** 사용 |
| 5 | 없으면 폴백: `entry ± (risk × rr_min)` |

**R:R 최소 기준** (컨플루언스 강도에 따라):

| 조건 | 최소 R:R (default) |
|---|---|
| HTF+MTF POI 겹침 있음 | `rr_min_strong` = 1.2 |
| HTF OB가 Active POI | `rr_min_with_poi` = 1.5 |
| 기본 | `rr_min_default` = 1.8 |

**R:R < 최소 기준이면 → 스킵** (TriggerEvent 생성 안 함)

---

### TriggerEvent 출력

모든 3단계 통과 + R:R 충족 시 `TriggerEvent` 생성:

```python
TriggerEvent(
    symbol="BTC/USDT",
    timestamp=...,
    direction="bullish",          # HTF 추세
    entry_price=70000.0,          # LTF 진입가
    stop_loss=68600.0,            # SL 우선순위 결과
    take_profit=73500.0,          # 반대편 유동성
    rr_ratio=2.5,                 # R:R
    entry_type="fvg_ce",          # 진입 방식
    session="뉴욕",
    confluences=[                 # LLM 참고용 (점수 없음)
        Confluence("HTF 추세 일치"),
        Confluence("HTF OB Active", "2개"),
        Confluence("MTF BOS/CHoCH"),
        Confluence("LTF FVG CE 진입"),
        Confluence("HTF+MTF POI 겹침", "1쌍"),
    ],
    htf_obs=[...], htf_fvgs=[...],   # 차트 시각화용
    mtf_obs=[...], mtf_fvgs=[...],
    ...
)
```

### 컨플루언스 항목 전체 목록

ICT가 감지하는 모든 컨플루언스. 점수 시스템 없이 이름만 기록하여 LLM에 전달.

| 계층 | 항목 | 조건 |
|---|---|---|
| **HTF** | HTF 추세 일치 | direction == htf_ms.trend |
| | HTF OB Active | is_poi == True |
| | HTF FVG Active | is_active == True |
| | HTF Liquidity Sweep | 방향 일치 BSL/SSL |
| | PDH/PDL Sweep | 전일 고/저 스윕 |
| **MTF** | MTF BOS/CHoCH | 방향 일치 구조 브레이크 |
| | MTF OB Active | 활성 OB |
| | MTF FVG Active | 활성 FVG |
| | MTF Liquidity Sweep | 방향 일치 |
| | HTF+MTF POI 겹침 | OB 또는 FVG 가격 범위 겹침 (최강) |
| **LTF** | LTF FVG CE 진입 | 현재가 ≈ FVG CE |
| | LTF FVG Fill 진입 | FVG 50%+ 채워짐 |
| | LTF IOFED 진입 | 연속봉 3+ 후 반전 |
| | Session Range Sweep | 이전 세션 레인지 스윕 |
| | LTF Liquidity Sweep | LTF BSL/SSL |
| | LTF BOS/CHoCH | LTF 구조 브레이크 |
