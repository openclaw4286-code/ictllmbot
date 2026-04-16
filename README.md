# ICT 자동 트레이딩 시스템

ICT(Inner Circle Trader) 전략 기반 암호화폐 선물 자동매매 봇.
알고리즘이 ICT 트리거를 감지하고 신호를 생성하면 LLM(Claude)이 최종 PASS/WAIT를 결정한다.

## 기술 스택

| 항목 | 사용 |
|---|---|
| 언어 | Python 3.9+ (asyncio) |
| 거래소 | Gate.io 선물 (ccxt) |
| 유니버스 | CoinGecko 시총 상위 30개 (스테이블코인 제외, 1시간 캐시) |
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

| 세션 | UTC | KST | 점심 제외 (UTC) | 점심 제외 (KST) |
|---|---|---|---|---|
| 아시아 | 00:00~06:00 | 09:00~15:00 | 03:00~04:00 | 12:00~13:00 |
| 런던 | 07:00~12:00 | 16:00~21:00 | 10:00~11:00 | 19:00~20:00 |
| 뉴욕 | 12:00~17:00 | 21:00~02:00 | 15:00~16:00 | 00:00~01:00 |

- 세션 외 시간(UTC 17:00~00:00, KST 02:00~09:00) 및 주말(토/일)은 스캔하지 않음
- 각 세션의 점심시간(1시간)은 유동성 저하로 스캔 제외
- 하루 총 활성: **13시간** (각 세션에서 점심 1시간씩 제외)

### 루프 설정

| 항목 | 값 | 설명 |
|---|---|---|
| `LOOP_INTERVAL_SECONDS` | 60 | 메인 루프 간격 (초) |
| `LLM_TIMEOUT_SECONDS` | 120 | Claude CLI 응답 대기 (초) |
| `LLM_MAX_CONCURRENT` | 3 | 동시 LLM 호출 최대 수 |
| `ECON_LOCK_MINUTES` | 30 | 경제지표 전후 잠금 (분) |
| WAIT 대기 시간 | 5~120분 | LLM이 동적으로 결정 |
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
    htf_obs=[...], htf_fvgs=[...],   # LLM 프롬프트용
    mtf_obs=[...], mtf_fvgs=[...],
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

---

## LLM 판단

**파일**: `llm/prompt_builder.py`, `llm/analyzer.py`

### 역할

LLM은 **방향/진입가/SL/TP를 결정하지 않는다**. 알고리즘이 모두 결정한 후, LLM은 **PASS 또는 WAIT만** 판단한다.

### 프롬프트 구성 (`prompt_builder.py`)

LLM에 전달되는 데이터:

| 항목 | 내용 |
|---|---|
| 시스템 프롬프트 | ICT 분석가 역할 정의, JSON 응답 형식 지시 |
| 신호 텍스트 | 심볼, 방향, 진입가/SL/TP, R:R, 세션, 컨플루언스 목록 |
| 뉴스 | CoinDesk/CoinTelegraph RSS에서 해당 코인 최근 5건 |
| 경제지표 | 향후 4시간 내 고영향 이벤트 |

LLM은 텍스트 기반으로만 판단. ICT 분석은 알고리즘이 이미 수행했으므로 차트 불필요.

### LLM 판단 기준 (시스템 프롬프트에 명시)

1. 컨플루언스 구성이 합리적인지
2. 뉴스가 포지션 방향에 역행하지 않는지
3. 고영향 경제지표가 임박해 변동성 리스크 있는지
4. 전반적 시장 컨텍스트가 진입에 적합한지

### LLM 응답 형식

```json
{
  "verdict": "PASS | WAIT",
  "reasoning": "3줄 이내 한국어 설명",
  "news_impact": "POSITIVE | NEGATIVE | NEUTRAL",
  "econ_risk": "HIGH | LOW",
  "wait_minutes": "WAIT일 때 재검토 대기 시간 (5~120분 정수), PASS면 null"
}
```

### Claude CLI 호출 (`analyzer.py`)

```
echo "{시스템프롬프트}\n---\n{신호+뉴스+경제}" | claude -p --model opus
```

- `asyncio.create_subprocess_shell`로 실행 (PATH 환경 상속)
- `ANTHROPIC_API_KEY` 환경변수 제거 (Max 로그인 인증 우선)
- `asyncio.Semaphore`로 동시 호출 `LLM_MAX_CONCURRENT`(3)개 제한

### LLM 에러 처리

| 상황 | verdict | 대기 시간 | 주문 실행? |
|---|---|---|---|
| LLM 정상 → PASS | PASS | - | **실행** |
| LLM 정상 → WAIT | WAIT | LLM이 결정 (5~120분) | 스킵 |
| LLM이 REJECT 반환 | → WAIT 변환 | 10분 | 스킵 |
| 타임아웃 (120초) | → WAIT | 10분 | 스킵 |
| Claude CLI 에러 (code≠0) | → WAIT | 10분 | 스킵 |
| JSON 파싱 실패 | → WAIT | 10분 | 스킵 |
| 그 외 예외 | → WAIT | 10분 | 스킵 |

**LLM 실패 시 절대 자동 PASS 없음.** 모든 에러는 WAIT(10분 대기)로 변환.

### PASS / WAIT 후속 처리

| | PASS | WAIT |
|---|---|---|
| **주문 실행** | 즉시 실행 | 스킵 |
| **대기 시간** | - | LLM이 반환한 5~120분 |
| **재시도** | - | 대기 시간 경과 후 ICT가 다시 분석, 신호 있으면 다시 LLM 호출 |

**WAIT 동작**: LLM이 "지금은 안 되지만 30분 후 다시 봐라"라고 판단하면 해당 심볼은 30분간 LLM 호출 스킵.
대기 시간 경과 후 ICT가 재분석하여 신호가 있으면 다시 LLM에 전달. 무한 재시도 아님 — 매번 새로운 시장 상황 기반.

### JSON 파싱 (`_parse_response`)

| 입력 형태 | 처리 |
|---|---|
| 순수 JSON `{"verdict": ...}` | 그대로 파싱 |
| 코드 블록 ` ```json ... ``` ` | 블록 내용 추출 후 파싱 |
| JSON 앞뒤에 텍스트 | `{` ~ `}` 사이만 추출 |
| 파싱 불가 | WAIT 반환 (10분 대기) |

### 판단 이력 저장

모든 LLM 판단은 `ict_trader/llm_decisions.json`에 누적 저장:

```json
{
  "timestamp": "2026-04-16T01:28:18+00:00",
  "symbol": "BTC/USDT",
  "direction": "bullish",
  "entry_type": "fvg_ce",
  "entry_price": 70000,
  "stop_loss": 68600,
  "take_profit": 73500,
  "rr_ratio": 2.5,
  "session": "뉴욕",
  "confluences_count": 5,
  "confluences": ["HTF 추세 일치", "HTF OB Active", ...],
  "verdict": "PASS",
  "reasoning": "HTF bullish 추세와 OB 확인, 진입 적합",
  "news_impact": "POSITIVE",
  "econ_risk": "LOW",
  "wait_minutes": null,
  "error": null
}
```

에러/타임아웃도 기록됨 (추적 가능).

---

## 주문 실행

**파일**: `execution/gate_executor.py`

### 포지션 사이징 (Fixed Fractional, ICT 2% 룰)

```
1. 리스크 금액 = 잔고 × RISK_PER_TRADE (2%)
2. 노셔널(포지션 크기) = 리스크 금액 / SL 거리%
3. 레버리지 = min(LEVERAGE_MAX, 1 / (SL × LIQUIDATION_SAFETY_BUFFER))
4. 증거금(margin) = 노셔널 / 레버리지
```

**예시** (잔고 $100, SL 2%):
```
리스크 = $100 × 2% = $2
노셔널 = $2 / 2% = $100
레버리지 = min(50, 1/(0.02×3)) = 16x
증거금 = $100 / 16 = $6.25
```

### 동적 레버리지 (`calculate_optimal_leverage`)

**공식**: `L = min(LEVERAGE_MAX, 1 / (SL거리 × LIQUIDATION_SAFETY_BUFFER))`

**의미**: 청산가가 SL보다 `LIQUIDATION_SAFETY_BUFFER`배(3배) 이상 멀도록 보장.

| SL 거리 | 이상적 L | 실제 L (상한 50) | 청산 거리 |
|---|---|---|---|
| 0.5% | 66.7 | **50x** | 2.0% (SL의 4배) |
| 1% | 33.3 | **33x** | 3.0% (3배) |
| 2% | 16.7 | **16x** | 6.3% (3배) |
| 3% | 11.1 | **11x** | 9.1% (3배) |
| 5% | 6.7 | **6x** | 16.7% (3배) |

### 동시 포지션

- **개수 제한 없음** (담보 남으면 계속 진입)
- 담보 부족 시 사전 체크: `기사용 margin + 이번 margin > 잔고`이면 스킵
- 거래소도 담보 부족 시 주문 자동 거부

### tick_size 정밀도 처리 (`adjust_order_precision`)

모든 주문은 반드시 이 함수를 통과:

| 방향 | SL | TP | amount |
|---|---|---|---|
| Long | **floor** (내림) | **ceil** (올림) | **floor** |
| Short | **ceil** (올림) | **floor** (내림) | **floor** |

- `tick_size`: Gate.io 마켓별 최소 가격 단위
- `amount_precision`: 최소 수량 단위
- `min_amount`: 최소 주문량 미만이면 주문 취소

### Gate.io 선물 계약 변환

Gate.io 선물은 코인 수량이 아닌 **계약(contract) 단위** 주문:

```
contract_size = 0.0001 BTC (Gate.io BTC 선물 기준)
contracts = coin_amount / contract_size
→ 최소 1계약 보장
```

### Gate.io 선물 심볼 변환

모든 주문에서 `BTC/USDT` → `BTC/USDT:USDT` 자동 변환.
현물이 아닌 선물 마켓으로 라우팅하기 위함.

### 주문 실행 흐름 (`execute_order`)

```
1. 코인 수량 → 계약 수 변환 (contract_size 기준)
2. 정밀도 조정 (adjust_order_precision)
3. 수량 0이면 → None 반환
4. PAPER_TRADING이면 → 로그만 기록, 가상 주문 ID 반환
5. 레버리지 설정 (exchange.set_leverage)
6. 시장가 주문 (market order + price 파라미터)
7. SL 주문 (stop order, reduceOnly)
8. TP 주문 (limit order, reduceOnly)
9. 주문 결과 dict 반환
```

### 주문 실행 에러 처리

| 상황 | 결과 |
|---|---|
| 정밀도 조정 후 수량 0 | 주문 취소, None 반환 |
| 레버리지 설정 실패 | 경고 로그, 기존값으로 계속 진행 |
| 시장가 체결 실패 | None 반환, 에러 로그 |
| SL 주문 실패 | **자동 긴급 청산** (시장가 반대 주문 + 미체결 주문 취소) |
| TP 주문 실패 | **자동 긴급 청산** |
| PAPER_TRADING | 실제 주문 없이 로그 기록, 가상 주문 ID |

---

## 포지션 관리

**파일**: `execution/position_manager.py`

### 포지션 추적

`PositionManager`가 메모리에서 모든 활성 포지션을 관리:

```python
Position(
    symbol, direction, entry_price, amount, margin,
    stop_loss, take_profit, order_id,
    sl_order_id, tp_order_id,
    rr_ratio, entry_type, session, opened_at
)
```

### 거래소 동기화 (`sync_from_exchange`)

60초마다 메인 루프에서 호출:

```
1. 거래소에서 실제 포지션 목록 조회 (fetch_positions)
2. 로컬에 있지만 거래소에 없는 포지션 = 청산됨 (SL/TP 체결)
3. 청산된 포지션에 대해 승/패 판정
4. trade_history.json에 기록
5. 로컬에서 포지션 제거
```

### 승/패 판정 (`_determine_win_loss`)

| 우선순위 | 방법 | 결과 |
|---|---|---|
| 1 | TP 주문 상태 조회 → "filled"/"closed" | **승 (True)** |
| 2 | SL 주문 상태 조회 → "filled"/"closed" | **패 (False)** |
| 3 | 폴백: 현재가 vs 진입가 방향 비교 | 방향 일치면 승 |
| 4 | 모든 조회 실패 | **패로 처리 (보수적)** |

### 거래 이력 영속화

`trade_history.json`에 모든 결과 저장 (봇 재시작 시 로드):

```json
[
  {"win": true,  "rr": 2.5, "symbol": "BTC/USDT",  "timestamp": "..."},
  {"win": false, "rr": 1.8, "symbol": "ETH/USDT",  "timestamp": "..."}
]
```

### 알림 (`execution/notifier.py`)

모든 알림은 콘솔 + 로그 파일에 출력:

```
============================================================
  LONG — BTC/USDT  |  R:R 1:2.5  |  3 confluences
  진입 $70,000.00  SL $68,600.00  TP $73,500.00
  켈리 베팅: 2.0%  |  포지션: 0.001 BTC
  [LLM 검토] HTF bullish 추세 확인, 진입 적합
  뉴스: 긍정적  |  경제지표 리스크: 낮음
============================================================
```

| 알림 함수 | 용도 |
|---|---|
| `notify_signal` | 신호 + LLM 판단 + 사이징 정보 |
| `notify_status` | 시스템 시작/종료 |
| `notify_error` | 에러 발생 |
| `notify_position_closed` | 포지션 종료 (사유 + PnL) |

---

## 메인 루프

**파일**: `main.py`

### 실행 주기

60초(`LOOP_INTERVAL_SECONDS`)마다 1사이클 실행. `Ctrl+C` (SIGINT) 또는 SIGTERM으로 종료.

### 1사이클 상세 흐름 (`_run_one_cycle`)

```
① 세션 체크
  → 세션 외 또는 주말이면 즉시 return

② 경제지표 잠금 체크
  → 고영향 이벤트 전후 30분이면 즉시 return

③ 포지션 동기화
  → 거래소 실제 포지션 조회, SL/TP 체결된 것 감지 → 승/패 기록

④ 잔액 갱신
  → Gate.io 선물 USDT 잔액 조회

⑤ 유니버스 조회
  → CoinGecko 시총 상위 30개 (1시간 캐시, Gate.io 마켓 검증)

⑥ 심볼 순회 (30개)
  ├─ 스킵 조건 4가지 체크
  ├─ HTF/MTF/LTF OHLCV 동시 수집 (asyncio.gather)
  ├─ 3단계 탑다운 분석 → TriggerEvent 또는 None
  └─ 뉴스 수집 (RSS, 해당 코인 필터링)
  → triggers 리스트에 추가

⑦ R:R 기준 정렬, 상위 3개만 LLM 검토 대상

⑧ 비동기 LLM 검토 + 주문 실행
  → asyncio.create_task로 병렬 LLM 호출
  → PASS 시 _execute_pass (Lock으로 직렬화)
  → asyncio.gather로 전부 완료 대기

⑨ 루프 통계 기록 (10루프마다 요약 로깅)
```

### 세션 체크 (①)

**파일**: `algorithm/session.py`

| 조건 | 동작 |
|---|---|
| UTC 요일 ≥ 5 (토/일) | return (주말 스킵) |
| 현재 UTC 시간이 SESSIONS에 없음 | return (세션 외) |
| 아시아 00~02 / 런던 07~09 / 뉴욕 13~15 내 | 계속 진행 |

### 경제지표 잠금 (②)

**파일**: `data/economic_calendar.py`

정기 이벤트 (매주 반복, UTC):

| 요일 | 시간 | 이벤트 |
|---|---|---|
| 화 | 15:00 | US PPI / CPI |
| 수 | 13:30 | US CPI / Retail Sales |
| 목 | 13:30 | US Jobless Claims |
| 금 | 13:30 | US Employment / NFP |
| FOMC일 | 19:00 | FOMC 금리 결정 (2026년 8회 수동 등록) |

+ FXStreet RSS에서 "nfp", "cpi", "fomc", "gdp", "fed" 등 키워드 매칭.

| 조건 | 동작 |
|---|---|
| `abs(현재시간 - 이벤트시간) ≤ ECON_LOCK_MINUTES` (30분) | return (잠금) |
| 이벤트 없음 또는 30분 밖 | 계속 진행 |
| FXStreet RSS 실패 | 정기 이벤트만으로 판정 (정상 폴백) |

### 유니버스 조회 (⑤)

**파일**: `data/universe.py`

```
CoinGecko API → 시총 상위 40개 요청 (스테이블코인 여유분)
  ↓
스테이블코인 16종 제외 (USDT, USDC, DAI, BUSD, FDUSD, ...)
  ↓
비표준 심볼 제외 (regex: ^[A-Z0-9]{2,10}$)
  ↓
Gate.io 선물 마켓에 없는 심볼 제외 (1시간 캐시)
  ↓
상위 30개 반환: ["BTC/USDT", "ETH/USDT", ...]
```

| 상황 | 결과 |
|---|---|
| CoinGecko API 실패 | 이전 캐시 반환 (만료돼도) |
| Gate.io 마켓 로드 실패 | 필터 비활성 (모든 CoinGecko 결과 통과) |
| 캐시 유효 (1시간 내) | API 호출 없이 캐시 반환 |

### OHLCV 수집 (⑥)

**파일**: `data/fetcher.py`

3개 타임프레임을 `asyncio.gather`로 동시 수집:

| 항목 | 설명 |
|---|---|
| 거래소 | Gate.io (ccxt async, 싱글턴 인스턴스) |
| 마켓 타입 | swap (선물) |
| rate limit | ccxt 내장 rate limiter 사용 |

| 상황 | 결과 |
|---|---|
| 특정 TF 수집 실패 | 해당 TF만 빈 DataFrame, 나머지 정상 |
| 3개 TF 중 하나라도 비어있음 | 탑다운 분석 스킵 |
| 거래소 연결 실패 | 에러 로그, 해당 심볼 스킵 |

### 뉴스 수집 (⑥)

**파일**: `data/news.py`

```
CoinDesk RSS + CoinTelegraph RSS (5분 캐시)
  ↓
심볼별 키워드 필터링 (예: BTC → "bitcoin", "btc", "crypto", "fed")
  ↓
최대 5건 반환
```

| 코인 | 매칭 키워드 |
|---|---|
| BTC | bitcoin, btc, crypto, market, fed, rate |
| ETH | ethereum, eth, ether |
| SOL | solana, sol |
| DOGE | dogecoin, doge |
| 기타 | 티커 소문자 (예: "xrp", "ada") |

| 상황 | 결과 |
|---|---|
| RSS 수집 실패 | 피드별 독립 처리, 실패한 건 스킵 |
| 해당 코인 뉴스 0건 | 전체 뉴스에서 상위 5건 반환 (폴백) |
| 캐시 유효 (5분 내) | API 호출 없이 캐시 반환 |

---

## 스킵 조건

**파일**: `state/loop_state.py`

심볼별로 4가지 조건을 순서대로 체크. 하나라도 해당되면 해당 심볼 스킵.

### 스킵 조건 4가지 (체크 순서)

| 순서 | 조건 | 해제 조건 | 통계 카운터 |
|---|---|---|---|
| 1 | **LLM 실행 중** | LLM 응답 수신 시 해제 | `total_skipped_llm` |
| 2 | **포지션 보유 중** | 포지션 종료(SL/TP) 시 해제 | `total_skipped_position` |
| 3 | **WAIT 쿨다운** | LLM이 결정한 시간(5~120분) 경과 시 해제 | `total_skipped_wait` |

### WAIT 동작

| LLM 결과 | 동작 |
|---|---|
| PASS | 즉시 주문 실행 |
| WAIT (30분) | 해당 심볼 30분간 LLM 호출 스킵 → 30분 후 ICT 재분석 → 신호 있으면 다시 LLM |
| 에러/타임아웃 | → WAIT 10분으로 변환 |

### 루프 통계

10루프(10분)마다 요약 로깅:

```
=== 루프 통계 (가동 2.5시간) ===
루프 150회 | 스캔 4500회
신호 12건 | LLM 8회
PASS 3 | WAIT 5
체결 3건
스킵: WAIT=200 포지션=120 LLM진행=5
```

---

## 실행 직렬화 (Race Condition 방지)

**문제**: 같은 루프에서 여러 PASS 신호가 `asyncio.create_task`로 병렬 실행 → 서로의 포지션을 모른 채 각자 증거금 사용 → 담보 초과.

**해결**: `asyncio.Lock`으로 `_execute_pass` 전체를 직렬화.

```
LLM 검토: 병렬 (3개 동시, 세마포어)
         ↓
주문 실행: 직렬 (Lock)
  ├─ PASS 1: 잔고 확인 → 사이징 → 주문 → 포지션 등록
  ├─ PASS 2: (Lock 대기) → 잔고 재확인 → 사이징 → 주문
  └─ PASS 3: (Lock 대기) → 잔고 재확인 → 담보 부족이면 스킵
```

| 항목 | LLM 검토 | 주문 실행 |
|---|---|---|
| 동시성 | **병렬** (Semaphore 3) | **직렬** (Lock) |
| 이유 | LLM 응답 시간 단축 | 증거금 정확한 계산 |

---

## 데이터 수집 및 영속화

### 자동 생성되는 파일

| 파일 | 내용 | 생성 시점 |
|---|---|---|
| `logs/ict_trader_YYYYMMDD.log` | 일별 로그 | 봇 시작 시 |
| `trade_history.json` | 거래 승/패 이력 | 포지션 종료 시 |
| `llm_decisions.json` | LLM 판단 이력 | LLM 호출마다 |

### trade_history.json 용도

- 봇 재시작 시 자동 로드 (이력 유지)
- 현재는 통계 집계용 (`get_trade_stats()`)
- 향후 Kelly/사이징 동적 조정의 기반 데이터

### llm_decisions.json 용도

- LLM이 왜 PASS/WAIT 했는지 추적
- 에러/타임아웃도 기록 (디버깅)
- 전략 개선 시 분석 데이터

### 확인 명령

```bash
# LLM 판단 통계
python3 -c "
import json
from collections import Counter
data = json.load(open('ict_trader/llm_decisions.json'))
print(f'총 {len(data)}건')
print(Counter(d['verdict'] for d in data))
"

# 거래 이력
python3 -c "
import json
data = json.load(open('ict_trader/trade_history.json'))
wins = sum(1 for d in data if d['win'])
print(f'총 {len(data)}건, 승 {wins}, 패 {len(data)-wins}')
if data: print(f'승률: {wins/len(data)*100:.1f}%')
"
```

---

## 리스크 관리 상세

### Fixed Fractional (2% 룰) 원리

**"거래당 SL 터치 시 잃을 금액 = 자산의 2%"**

이게 전부. 레버리지/포지션 크기/동시 포지션 수는 모두 여기서 파생:

```
① 리스크 금액 = 잔고 × 2%                        ← 고정
② 포지션 크기(notional) = 리스크 / SL거리%         ← SL에 따라 변동
③ 레버리지 = min(50, 1/(SL×3))                    ← SL에 따라 자동
④ 증거금(margin) = notional / 레버리지              ← 파생 결과
⑤ 동시 포지션 = 잔고 / margin (담보 남는 한)        ← 자연 결정
```

### 리스크 = 일정, 나머지는 SL이 결정

| SL 거리 | 리스크 | notional | 레버리지 | margin | $100에서 최대 포지션 수 |
|---|---|---|---|---|---|
| 0.5% | $2 | $400 | 50x | $8 | 12개 |
| 1% | $2 | $200 | 33x | $6 | 16개 |
| 2% | $2 | $100 | 16x | $6.25 | 16개 |
| 3% | $2 | $67 | 11x | $6 | 16개 |
| 5% | $2 | $40 | 6x | $6.67 | 15개 |

**포인트**: 리스크($2)는 항상 같음. SL이 짧으면 큰 포지션 → 높은 레버리지 → 많은 증거금.

### 레버리지와 청산

| 항목 | 공식 | 의미 |
|---|---|---|
| 청산 거리 | ~1/레버리지 | 레버리지 16x → 약 6.3% 역행 시 청산 |
| 안전 배수 | 3.0 | 청산 거리는 SL의 3배 이상 |
| SL 2% → 청산 6% | SL 터치 시점에 충분한 여유 | 플래시 크래시 3% 발생해도 청산 안 됨 |

### 담보 기반 자연 제한

동시 포지션 수는 설정으로 제한하지 않음. 담보가 떨어지면 자연 중단:

```python
total_used = position_manager.get_margin_usage() * balance
if total_used + margin > balance:
    return  # 담보 부족, 이번 포지션 스킵
```

거래소도 추가로 거부 (margin insufficient error).

---

## 에러 처리 총정리

### 데이터 수집 단계

| 모듈 | 에러 | 처리 |
|---|---|---|
| `universe.py` | CoinGecko API 실패 | 이전 캐시 반환 (만료돼도) |
| `universe.py` | Gate.io 마켓 로드 실패 | 필터 비활성, 모든 심볼 통과 |
| `fetcher.py` | 특정 TF OHLCV 실패 | 해당 TF만 빈 DataFrame |
| `fetcher.py` | 3TF 중 하나 빈 DataFrame | 해당 심볼 탑다운 분석 스킵 |
| `fetcher.py` | 거래소 연결 실패 | 해당 심볼 스킵, 에러 로그 |
| `news.py` | RSS 피드 실패 | 피드별 독립, 실패한 피드 스킵 |
| `news.py` | 해당 코인 뉴스 0건 | 전체 뉴스 상위 5건 폴백 |
| `economic_calendar.py` | FXStreet RSS 실패 | 정기 이벤트만으로 판정 |

### 알고리즘 단계

| 모듈 | 상황 | 처리 |
|---|---|---|
| `market_structure.py` | 데이터 < `swing_bars×2+1` 봉 | 빈 결과 반환, 분석 불가 |
| `trigger.py` | HTF 추세 neutral | 해당 심볼 스킵 |
| `trigger.py` | MTF BOS/CHoCH + OB/FVG 모두 없음 | 해당 심볼 스킵 |
| `trigger.py` | LTF 진입 조건 3가지 모두 미충족 | 해당 심볼 스킵 |
| `trigger.py` | R:R < 최소 기준 | 해당 심볼 스킵 |

### LLM 단계

| 상황 | verdict | 주문 실행 | llm_decisions.json |
|---|---|---|---|
| LLM 정상 → PASS | PASS | **실행** | 기록 |
| LLM 정상 → WAIT | WAIT | 스킵 (LLM 결정 시간) | 기록 |
| LLM이 REJECT 반환 | → WAIT | 스킵 (10분) | 기록 |
| 타임아웃 (120초) | → WAIT | 스킵 (10분) | 기록 (error="timeout") |
| Claude CLI 에러 (code≠0) | → WAIT | 스킵 (10분) | 기록 (error=메시지) |
| JSON 파싱 실패 | → WAIT | 스킵 (10분) | 기록 (error=파싱에러) |
| 그 외 예외 | → WAIT | 스킵 (10분) | 기록 |

**LLM 실패 시 자동 PASS는 절대 없음.**

### 주문 실행 단계

| 상황 | 처리 |
|---|---|
| `calculate_bet_fraction` | 항상 RISK_PER_TRADE(2%) 반환 (실패 없음) |
| `get_balance()` 실패 | $0 반환 → margin 0 → 진입 안 함 |
| 담보 부족 (기사용 + 신규 > 잔고) | 로그, 진입 안 함 |
| `get_tick_size()` 실패 | 에러 로그, 진입 안 함 |
| 정밀도 조정 후 수량 0 | 주문 취소, None 반환 |
| 레버리지 설정 실패 | 경고 로그, 기존값으로 진행 |
| 시장가 주문 실패 | None 반환 (SL/TP도 안 걸림) |
| SL 주문 실패 | 에러 로그, **시장가는 이미 체결됨** |
| TP 주문 실패 | 에러 로그, **시장가는 이미 체결됨** |
| PAPER_TRADING=True | 실제 주문 없이 로그만 기록 |

**SL/TP 실패 시 주의**: 시장가는 체결됐지만 SL/TP가 안 걸린 상태. 수동 SL/TP 설정 또는 포지션 청산 필요.

### 포지션 관리 단계

| 상황 | 처리 |
|---|---|
| 거래소 포지션 조회 실패 | 에러 로그, 이번 동기화 스킵 |
| TP 주문 상태 조회 실패 | SL 조회로 넘어감 |
| SL 주문 상태 조회 실패 | 현재가 vs 진입가 비교로 폴백 |
| 현재가 조회도 실패 | **패로 처리** (보수적) |
| PAPER_TRADING | 거래소 동기화 전체 스킵 |

### 메인 루프 단계

| 상황 | 처리 |
|---|---|
| 세션 외 / 주말 | 즉시 return (다음 60초 후 재시도) |
| 경제지표 잠금 | 즉시 return |
| 유니버스 비어있음 | 경고 로그, return |
| 심볼별 OHLCV 실패 | 해당 심볼 스킵, 나머지 계속 |
| 탑다운 분석 중 예외 | 해당 심볼 스킵 |
| `_process_trigger` 예외 | 에러 로그, WAIT 10분 처리 |
| 루프 전체 예외 | 에러 로그 + 알림, 루프 계속 (봇 안 죽음) |
| SIGINT/SIGTERM | `_shutdown=True`, 현재 주문 완료 후 종료 |

---

## 설치 및 실행

### 필요 사항

- Python 3.9+
- Node.js 18+ (Claude CLI용)
- Claude Max 구독 (터미널 로그인)
- Gate.io API 키 (선물 권한)
- Gate.io 선물 계좌에 USDT

### 설치

```bash
git clone https://github.com/openclaw4286-code/ictllmbot.git
cd ictllmbot
git checkout claude/ict-trading-system-Ghpxm

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r ict_trader/requirements.txt

npm install -g @anthropic-ai/claude-code
claude login
```

### .env 설정

```bash
cat > ict_trader/.env <<EOF
GATE_API_KEY=실제_API_키
GATE_API_SECRET=실제_SECRET
EOF
```

### 실행

```bash
python3 -m ict_trader.main
```

### 백그라운드 실행 (터미널 닫아도 유지)

```bash
nohup python3 -m ict_trader.main > bot.log 2>&1 &
tail -f bot.log        # 로그 확인
pkill -f "ict_trader"  # 종료
```

### 전체 포지션 청산 (긴급)

```python
# Google Colab 또는 Python에서:
import ccxt
exchange = ccxt.gateio({
    "apiKey": "KEY", "secret": "SECRET",
    "options": {"defaultType": "swap"},
})
for pos in exchange.fetch_positions():
    if float(pos.get("contracts", 0)) > 0:
        side = "sell" if pos["side"] == "long" else "buy"
        exchange.create_order(
            pos["symbol"], "market", side,
            float(pos["contracts"]),
            float(pos.get("entryPrice", 0)),
            params={"reduceOnly": True},
        )
```
```
