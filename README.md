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
