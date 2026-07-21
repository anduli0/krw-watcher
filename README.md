# KRW-Watcher 🇰🇷💵

원-달러(USD/KRW) 환율 경로를 예측하는 기관급 멀티에이전트 시스템.
[Fed-Watcher](../fed-watcher)와 동일한 설계 원리(전문 에이전트 위원회 → 2라운드 협업 →
베이지안 가중집계 → 안정화)를 환율 예측으로 옮기고, **증권사 API 연동을 위한 트레이딩
브릿지**를 추가했습니다.

> 목표: 검증된 예측 엔진 → 모의매매로 트랙레코드 축적 → 증권사 API 연결 → 외환 AI 매매.

---

## 부호 규약 (반드시 숙지)

```
delta_krw > 0  → USD/KRW 상승 → 원화 약세 → signal "krw_weak"   → USD/KRW LONG
delta_krw < 0  → USD/KRW 하락 → 원화 강세 → signal "krw_strong"  → USD/KRW SHORT
|delta| < 6원   → "neutral" (관망)
```

호라이즌: **1주(1w) · 1개월(1m) · 3개월(3m) · 1년(12m)**. 모든 델타는 현재 스팟 대비 원(₩).

---

## 에이전트 구성 (20)

요청하신 기관 유형별 렌즈로 구성했습니다.

### 전문 데스크 (13) — 연준 / 재무부 / 한국은행 / 국제기구 / 학계 / 경제이론
| # | 에이전트 | 렌즈 |
|---|----------|------|
| 1 | `Fed_Policy` | 연준 통화정책·실질금리·QT → 달러 |
| 2 | `BOK_Policy` | 한국은행 기준금리·물가·외환당국 개입 반응함수 |
| 3 | `Rate_Carry` | 미-한 금리차·캐리·NDF/스왑 베이시스 |
| 4 | `US_Fiscal_Dollar` | 미 재무부 국채발행·재정·달러지수(DXY) 레짐 |
| 5 | `Korea_External` | 경상수지·반도체 수출 사이클·외환보유고·국민연금 환헤지 |
| 6 | `Global_Risk` | VIX·위험선호·달러 스마일·외국인 코스피 자금 |
| 7 | `Technical_Flow` | 가격·모멘텀·심리적 레벨·포지셔닝 |
| 8 | `Intl_Bodies` | IMF Article IV·BIS·OECD 밸류에이션(REER) |
| 9 | `Academic_FX` | UIP·PPP·Dornbusch 오버슈팅·BEER/FEER |
| 10 | `CNY_Asia_EM` | 위안화 프록시·PBoC 고시·아시아 EM 베타 |
| 11 | `Consensus` | 전 렌즈 통합 하우스뷰 (프리미엄 가중) |
| 12 | `Monetary_BoP` | **통화량(M2 상대증가율)·국제수지(경상+자본수지)·한미 금리차·자본 유출입** — 통화모형·BoP 접근 |
| 13 | `Market_Linkage` | **주식(코스피 외국인 자금·S&P 위험선호)·채권(KTB 외국인·금리차) 시장 연계** |

### 셀사이드 은행·증권사 패널 (7) — 금융사/증권사
`Desk_GS`, `Desk_JPM`, `Desk_MS`, `Desk_Nomura`, `Desk_Citi`, `Desk_Samsung_Sec`(삼성증권),
`Desk_Mirae`(미래에셋) — 각 하우스의 공개된 분석 스타일을 모사한 페르소나
(실제 사 예측치가 아님). Fed-Watcher의 12개 지역연준 패널에 대응합니다.

### 데이터 소스
- **시장/매크로**: FRED(연준 금리·UST·CPI·DXY·VIX·USD/CNY·DEXKOUS), yfinance `KRW=X` 인트라데이 스팟, 한국은행 ECOS(기준금리, 선택).
- **공신력 페이퍼/언론**: WSJ · Financial Times · Bloomberg · Reuters · **Harvard Business Review** · Project Syndicate · 연합인포맥스 · 한국은행/기획재정부 (`backend/briefing/sources.py`).

---

## 파이프라인

```
30분마다  데이터 수집 (토큰 0)  ──┐
                                  ├─→  AgentContext 구성
2시간마다 + 장 시작/마감  AI 사이클 ┘
   │
   ├─ Round 1: 18 에이전트 독립 분석 (4 호라이즌)
   ├─ Round 2: 1개월 이견(outlier) 에이전트가 컨센서스 보고 수정
   ├─ 베이지안 정밀도 가중집계 + 위원회 이견 보정
   ├─ Chief 도출 리포트 (한/영)
   ├─ 안정화 (EMA + 컨빅션 게이트 + 양자화)
   └─ 트레이드 신호 생성 → 브로커 실행 (Paper 즉시 / KIS 게이트)
```

---

## 트레이딩 브릿지 (증권사 연동의 핵심)

`backend/signals/trade_signal.py` 가 예측을 포지션으로 변환합니다:
방향(LONG/SHORT/FLAT) · 진입(스팟) · 목표 · 손절 · **신뢰도 비례 사이즈**
(`MAX_TRADE_NOTIONAL_USD` 상한, 컨빅션 플로어 미만은 FLAT).

`backend/brokers/` 는 **하나의 추상 인터페이스**(`BrokerAdapter`)로 모든 체결을 처리합니다:
- `PaperBroker` — 실시간 스팟 기반 시뮬레이션 체결, 즉시 사용 가능 (트랙레코드 축적용).
- `KISBroker` — **한국투자증권 KIS Developers** 스캐폴드. OAuth 토큰 발급은 구현, **주문은
  의도적으로 게이트**(`ENABLE_LIVE_TRADING=true` + 상품/엔드포인트 직접 연결 전까지 차단).

> 브로커 교체는 `.env`의 `BROKER=paper|kis` 한 줄로 끝납니다 — 엔진 코드 수정 불필요.

### ⚠️ 실거래 안전장치
- 기본값 `BROKER=paper`, `ENABLE_LIVE_TRADING=false` → 실주문 절대 불가.
- 모든 주문은 `MAX_TRADE_NOTIONAL_USD` 상한 검증.
- 라이브 전환 전 **반드시 모의(paper)로 트랙레코드 검증** 후 KIS 주문 엔드포인트 연결.
- 외환 마진/USD 선물 거래는 계좌·상품 자격이 필요합니다(증권사 약관 확인). 시스템은 신호를
  생성·시뮬레이션할 뿐, 투자 책임은 사용자에게 있습니다.

---

## 실행

```powershell
# 1) 단발 테스트 (서버 없이 한 사이클 → 터미널 출력)
.\run.ps1 -Once

# 2) API 서버 + 대시보드
.\run.ps1                       # http://localhost:8010  (브라우저에서 열면 대시보드)
```

브라우저로 **http://localhost:8010** 에 접속하면 실시간 대시보드가 뜹니다: 4개 호라이즌
예측 카드, 트레이드 신호·브로커 잔고, 18 에이전트 위원회 표, 도출 리포트, 실시간 활동 피드,
그리고 "사이클 실행" 버튼. Next.js 빌드 불필요(FastAPI가 정적 서빙).

`.env` 에 최소 `ANTHROPIC_API_KEY` 와 `FRED_API_KEY` 를 채우세요(FRED 키 없으면 스팟은
yfinance로만, 매크로는 비게 됩니다). BOK ECOS 키는 선택입니다.

### 주요 API
| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 실시간 대시보드 (IP 화이트리스트) |
| GET | `/health` | 상태·스팟·실현변동성·브로커 |
| GET | `/api/forecast` | 4 호라이즌 예측 + 변경설명 + 도출 리포트(한/영) |
| GET | `/api/forecast/history?horizon=1m` | 호라이즌별 예측 이력 |
| GET | `/api/signal` | 최신 트레이드 신호 + 브로커 잔고 |
| GET | `/api/agents` | 최근 사이클 에이전트별 출력 |
| GET | `/api/positions` | 보유 포지션 |
| GET | `/api/accuracy` | 실현 적중률·MAE·승률·캘리브레이션 |
| POST | `/api/backtest?years=12` | 과거 USD/KRW로 트레이드 룰 백테스트 |
| GET | `/api/activity?after=N` | 실시간 활동 피드 |
| POST | `/api/cycle` | 수동 사이클 트리거 |
| GET | `/api/briefing/latest` | 최신 데일리 브리프 |
| POST | `/api/briefing/generate` | 브리프 생성 + 텔레그램 발송 |
| POST | `/auth/login` | 관리자 로그인 → JWT 쿠키 |
| GET/POST | `/api/admin/weights` | 에이전트 가중치 조정 (admin 전용) |

### 🔒 보안 (Fed-Watcher 동일 모델 — "웹사이트 IP")
돈이 걸린 사설 시스템이므로 3중 잠금:
- **IP 화이트리스트** — `ALLOWED_IPS` (리버스 프록시의 `X-Forwarded-For` 신뢰). `"*"` = 공개.
- **하드웨어 락** — `OWNER_MAC`. .env+DB를 다른 기기로 복사해도 실행 거부 (DEV_MODE 시 생략).
- **JWT 인증** — 관리자 패널/가중치는 bcrypt 패스워드 로그인 → HS256 JWT(20h) 필요.

운영 전환: `python setup.py` 1회 실행 (MAC 감지 · JWT 시크릿 생성 · 패스워드 해시 · `DEV_MODE=false`).
로컬 개발은 `DEV_MODE=true`로 MAC/JWT 생략(IP 화이트리스트는 유지).

### 📊 정밀도 · 검증 (돈 거는 데 필수)
- **백테스트 하니스** (`backtest/`) — 과거 USD/KRW(FRED DEXKOUS)로 트레이드 룰(진입·목표·손절·R:R·홀딩)을 검증. 승률·총수익·방향적중률·Sharpe·MDD·Profit Factor 산출. 토큰 0(투명한 추세 베이스라인을 프록시로 사용 — 12년 검증 결과 나이브 신호엔 엣지 없음을 수치로 입증).
- **라이브 정확도** (`accuracy/`) — 호라이즌 경과 시 실현환율 vs 예측 자동 채점: 호라이즌별 적중률·MAE, 에이전트 랭킹, 모의매매 승률·Sharpe·**신뢰도 캘리브레이션 곡선**.
- **5단계 적응형 안정화** — 변동성 레짐(저/정상/고)·이벤트·콜드스타트별 EMA alpha + 서브양자 게이트 + 컨빅션 게이트 + 변경설명(왜 바뀌었는지 LLM이 한국어로 설명).

---

## 구조

```
krw-watcher/
├─ backend/
│  ├─ config.py                 환경설정
│  ├─ main.py                   FastAPI + 사이클 오케스트레이션 + 체결 라우팅
│  ├─ agents/                   base_agent + 11 전문 + bank_desks(7) + orchestrator
│  ├─ data/                     fx_client(FRED/spot) · bok_client · collector · activity_log
│  ├─ briefing/                 sources(WSJ/FT/HBR…) · news
│  ├─ auth/                     mac_validator · jwt_handler · security · middleware  ← 보안
│  ├─ stabilizer/               5단계 적응형 EMA · 컨빅션 게이트 · 변경설명 · 이벤트 캘린더
│  ├─ signals/                  trade_signal (예측→포지션) · position_manager (자동청산)
│  ├─ risk/                     risk_manager (노출상한·일손실한도·변동성 스로틀)
│  ├─ feedback/                 feedback_loop (실현환율 vs 예측 → 적응형 학습)
│  ├─ backtest/                 history · engine (트레이드 룰 과거 검증)
│  ├─ accuracy/                 metrics (적중률·MAE·승률·캘리브레이션)
│  ├─ briefing/                 news · sources · generator · telegram (데일리 브리프→텔레그램)
│  ├─ brokers/                  base · paper · kis · factory  ← 증권사 연동 시앰
│  ├─ static/                   index.html (대시보드: 게이지·정확도·백테스트)
│  ├─ database/                 models · crud · init_db (SQLite, Postgres 호환)
│  └─ routes/                   auth · dashboard · admin · accuracy
├─ setup.py                     최초 1회 보안 설정 (MAC·JWT·패스워드)
├─ run_once.py                  단발 사이클 CLI
├─ run.ps1                      런처
├─ requirements.txt
└─ .env.example
```

## 구현 완료 / 로드맵

**이미 구현됨 (전부 실데이터·실API로 검증 완료)**
- ✅ 18 에이전트 위원회 + 2라운드 협업 + 베이지안 집계 + 5단계 적응형 안정화 + 변경설명
- ✅ **보안 3중 잠금** (`auth/`) — IP 화이트리스트 + 하드웨어(MAC) 락 + JWT/bcrypt 관리자 인증. `setup.py`로 운영 전환.
- ✅ **백테스트 하니스** (`backtest/`) — 과거 USD/KRW로 트레이드 룰 검증 (승률·수익·방향적중률·Sharpe·MDD).
- ✅ **라이브 정확도** (`accuracy/`) — 호라이즌별 적중률·MAE·에이전트 랭킹·승률·신뢰도 캘리브레이션.
- ✅ **피드백 루프** (`feedback/`) — 실현 환율 vs 예측 → `FeedbackEntry` → 적응형 가중치 + 부정 예시 주입.
- ✅ **리스크 매니저** (`risk/`) — 일 손실 한도·총 노출 상한·실현변동성 스로틀. 주문 직전 게이트.
- ✅ **Paper 브로커** 즉시 체결 + 자동청산(목표/손절/시간) · P&L 추적.
- ✅ **대시보드** (Fed-Watcher 포맷) — 토글 아코디언 섹션(제목 클릭 시 열림/닫힘), **실시간 에이전트 위원회 그리드**(라이브 피드로 분석중/완료 점등), **신뢰도 평가**(높음/보통/낮음 + 위원회 합의도 + 신호 분포), 게이지·정확도·백테스트.
- ✅ **데일리 브리프 + 텔레그램** (`briefing/`) — 예측+에이전트 분석+뉴스를 전문 한국어 브리프로 종합 → 매일 08:10 KST 텔레그램 자동 발송(+수동 버튼/엔드포인트).

**다음 단계 (실거래로)**
1. **검증** — Paper 브로커로 수 주간 트랙레코드 축적, 호라이즌별 적중률 측정.
2. **KIS 연결** — 거래 상품(원/달러 선물 등) 확정 → `kis_broker.place_order` 엔드포인트·TR_ID 연결 → 모의투자 도메인 검증.
3. **포지션 청산 로직** — 목표/손절 도달 시 자동 청산 + 실현손익 기록(현재는 진입까지). 
4. **실거래** — 소액 + 엄격한 상한으로 단계적 전환 (`ENABLE_LIVE_TRADING=true`).
