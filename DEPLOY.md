# KRW-Watcher 배포 가이드 (DEPLOY.md)

USD/KRW 환율을 23-에이전트 위원회로 예측하는 FastAPI 앱의 배포 가이드입니다.
엔트리포인트는 `backend.main:app`, 대시보드는 `/`(정적 `backend/static/index.html`), 헬스체크는 `GET /health`입니다.

---

## 1. 개요 (Overview)

KRW-Watcher는 한 프로세스 안에서 다음을 동시에 수행합니다.

- **FastAPI 앱**: 대시보드(`/`) + JSON API(`/api/*`) 서빙, `/docs` Swagger 제공
- **APScheduler** (lifespan에서 기동): 데이터 수집 / AI 사이클 / 데일리 브리프 스케줄링
- **인메모리 상태**: `activity_log` 링 버퍼, `runtime_state`(계층 합성 결과), 데이터 캐시, 프로세스 단위 broker 싱글톤
- **Anthropic API(유료)** 호출로 23-에이전트 위원회 사이클 실행

> ## ⚠️ 단일 인스턴스 규칙 (SINGLE-INSTANCE RULE) — 반드시 지킬 것
>
> **이 앱은 워커/레플리카를 1개만 띄워야 합니다.**
>
> 앱은 (1) lifespan 안에서 **APScheduler**를 돌리고, (2) **프로세스 단위 인메모리 상태**
> (`activity_log` 링 버퍼, `runtime_state`, 데이터 캐시, broker 싱글톤)를 보유합니다.
> 워커/레플리카를 2개 이상 띄우면:
>
> - 스케줄러가 **N번** 돌아 **Anthropic 토큰을 N배** 태웁니다.
> - 인메모리 상태가 인스턴스마다 **쪼개져** 대시보드가 인스턴스별로 다른 값을 보입니다.
>
> 따라서 시작 명령은 **반드시** 다음 형태여야 합니다.
>
> ```bash
> uvicorn backend.main:app --host 0.0.0.0 --port <PORT> --workers 1
> ```
>
> - `--workers`를 생략해도 됩니다(uvicorn 기본값이 1).
> - **`gunicorn -w >1` 금지. 레플리카 `>1` 금지.**
> - PaaS의 인스턴스/레플리카 수 설정은 항상 **1**로 고정하세요
>   (Render `numInstances: 1`, Fly `min_machines_running = 1` + `fly scale count 1`, compose `--scale app=1`).

저장소에는 다음 배포 산출물이 이미 포함되어 있습니다(이 가이드가 참조).
`Dockerfile`, `docker-compose.yml`, `render.yaml`, `fly.toml`, `Procfile`,
`deploy/Caddyfile`, `.dockerignore`, `.env.production.example`.

---

## 2. 프로덕션 보안 체크리스트 (PRODUCTION SECURITY CHECKLIST)

공개 배포 전에 아래를 **순서대로** 수행하세요.

1. **`DEV_MODE=false`로 설정.**
   `DEV_MODE=true`는 MAC 잠금 + JWT 인증을 우회하고 보호 라우트를 무조건 admin으로 통과시킵니다(개발 전용). 프로덕션에서는 반드시 `false`.

2. **`python setup.py` 실행해 보안 값 생성.**
   이 스크립트는 다음을 자동으로 `.env`에 채워 넣고 `DEV_MODE=false`로 뒤집습니다.
   - `JWT_SECRET` — 64 hex 문자(랜덤, ≥32자 충족)
   - `ADMIN_PASSWORD_HASH` — 입력한 admin 비밀번호의 **bcrypt 해시**
   - `OWNER_MAC` — 이 머신의 MAC(하드웨어 잠금; 비우면 잠금 해제)
   - `ALLOWED_IPS` — 프롬프트에서 입력(공란=`127.0.0.1`, `*`=공개)

   > PaaS(컨테이너) 배포에서는 보통 setup.py를 **로컬에서 한 번** 돌려 값을 만든 뒤,
   > 생성된 `JWT_SECRET`/`ADMIN_PASSWORD_HASH`를 PaaS 환경변수 대시보드에 붙여넣습니다.
   > 컨테이너의 MAC은 매번 바뀌므로 클라우드에서는 `OWNER_MAC`을 **비워 두는 것**이 안전합니다.

3. **공개 배포에서는 라이브 트레이딩을 끈 채로 유지.**
   - `ENABLE_LIVE_TRADING=false` (실주문 마스터 킬스위치)
   - `BROKER=paper`
   - `KIS_PAPER=true`

4. **`ALLOWED_IPS` 결정.**
   - `*` = 공개 — **읽기 엔드포인트는 누구나** 조회 가능. 단, 토큰을 태우거나 상태를 바꾸는 컨트롤은 여전히 admin 로그인 또는 `X-Cron-Secret`을 요구합니다(섹션 3 참조).
   - `203.0.113.7,198.51.100.0/24` 처럼 IP/CIDR CSV로 좁히면 그 IP에서만 접근 가능(리버스 프록시 뒤에서는 `X-Forwarded-For`의 첫 IP를 신뢰).

5. **외부 크론을 쓸 경우 `CRON_SECRET` 설정.**
   값이 설정되면 외부 스케줄러/웹훅이 `X-Cron-Secret: <값>` 헤더로 로그인 없이 `/api/cycle`, `/api/briefing/generate`를 호출할 수 있습니다. 비워 두면 헤더 기반 트리거가 비활성화됩니다.

6. **`.env`를 절대 커밋/이미지에 넣지 말 것.**
   저장소의 실제 `./.env`에는 **라이브 API 키**가 들어 있습니다. 이미 `.dockerignore`(`.env`, `.env.*`)와 `.gitignore`로 제외되어 있는지 확인하세요. 모든 시크릿은 **런타임 환경변수**로만 주입합니다.

---

## 3. `ALLOWED_IPS=*`일 때 공개 vs 보호 (Public vs Protected)

IP 화이트리스트를 `*`(공개)로 두면 보안 미들웨어(`backend/auth/middleware.py`)는 두 번째 계층(인증)만 적용합니다.

### 공개 (IP 화이트리스트만, 로그인 불필요)
대시보드와 읽기 전용 API:

- `GET /` (대시보드), `GET /health`, `GET /docs`
- `GET /api/forecast`, `GET /api/forecast/history`
- `GET /api/signal`, `GET /api/positions`
- `GET /api/agents`, `GET /api/hierarchy`
- `GET /api/accuracy` 계열, `GET /api/news`, `GET /api/activity`
- `GET /api/briefing/latest`

### 보호 (admin JWT **또는** `X-Cron-Secret` **또는** `DEV_MODE=true` 필요)
토큰을 태우거나 돈/상태를 바꾸는 모든 엔드포인트:

- `POST /api/cycle` — AI 사이클 실행(토큰 소모, 트레이드 발생 가능)
- `POST /api/briefing/generate` — 브리프 생성 + 텔레그램 발송(토큰 소모)
- `/api/backtest` — 백테스트 실행(소유자 FRED 쿼터 소모 + 공유 캐시 변경)
- `/api/admin/*` — 설정 / 가중치 / 피드백
- `/admin-secure-panel/*` — 관리자 패널

보호 라우트 통과 방법(미들웨어 우선순위):
1. `DEV_MODE=true`면 무조건 통과(개발 전용 — 프로덕션에서 쓰지 말 것).
2. `X-Cron-Secret` 헤더가 `CRON_SECRET`과 일치하면 통과(로그인 불필요).
3. admin JWT — `POST /auth/login`으로 발급받은 쿠키(`access_token`) 또는 `Authorization: Bearer <token>`.

> 로그인: `POST /auth/login` 바디 `{"password": "...", "role": "admin"}`.
> `ADMIN_PASSWORD_HASH`가 비어 있으면 503을 반환하며 `python setup.py`를 안내합니다.

---

## 4. 환경변수 표 (ENV VAR TABLE)

모든 시크릿은 **런타임 환경변수**로만 주입합니다(이미지/커밋 금지). 기본값은 `backend/config.py` 기준.

| 이름 | 필수? | 용도 | 프로덕션 권장값 |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ (AI 사이클) | 23-에이전트 위원회 호출(유료) | 실제 키 |
| `MODEL_ID` | ❌ | 사용할 Anthropic 모델 id | 비우면 앱 내장 기본값 사용 |
| `FRED_API_KEY` | 권장 | FRED 매크로/금리/스팟 폴백 데이터 | 실제 키 |
| `BOK_ECOS_KEY` | 권장 | 한국은행 ECOS(원화측 매크로) | 실제 키 |
| `DATABASE_URL` | ✅ (프로덕션) | DB 연결 문자열 | `postgresql+asyncpg://USER:PASS@HOST:5432/DB` (로컬 기본은 SQLite) |
| `DEV_MODE` | ✅ | 인증/IP 게이트 우회 토글 | `false` |
| `JWT_SECRET` | ✅ (DEV_MODE=false) | JWT 서명 키(≥32자) | setup.py 생성값 |
| `ADMIN_PASSWORD_HASH` | ✅ (DEV_MODE=false) | admin 비밀번호 bcrypt 해시 | setup.py 생성값 |
| `OWNER_MAC` | ❌ | 하드웨어 잠금 MAC(빈 값=잠금 해제) | 클라우드는 비움 |
| `ALLOWED_IPS` | ✅ | IP 화이트리스트 CSV(`*`=공개) | `*` 또는 본인 IP/CIDR |
| `CORS_ORIGINS` | ❌ | CORS 허용 오리진 CSV | 대시보드 오리진, 예 `https://krw.example.com` |
| `CRON_SECRET` | ❌ | 외부 크론 트리거 공유 시크릿(`X-Cron-Secret`) | 외부 크론 쓸 때만 설정 |
| `DISABLE_AUTO_CYCLE` | ❌ | true=무료 데이터 전용 미러(AI 사이클/브리프 끔) | 비용 통제 시 `true` |
| `BRIEFING_HOUR_KST` | ❌ | 데일리 브리프 생성 시각(KST, 0–23) | `8` |
| `TELEGRAM_BOT_TOKEN` | ❌ | 텔레그램 봇 토큰(브리프/알림) | 봇 토큰 |
| `TELEGRAM_CHAT_ID` | ❌ | 수신 chat id CSV | 본인 chat id |
| `BROKER` | ✅ | 브로커 백엔드(`paper`/`kis`) | `paper` |
| `ENABLE_LIVE_TRADING` | ✅ | 실주문 마스터 킬스위치 | `false` |
| `KIS_PAPER` | ❌ | KIS 모의/샌드박스 여부 | `true` |
| `KIS_APP_KEY` | ❌ | KIS 앱 키(BROKER=kis일 때만) | 비움 |
| `KIS_APP_SECRET` | ❌ | KIS 앱 시크릿 | 비움 |
| `KIS_ACCOUNT_NO` | ❌ | KIS 계좌번호 | 비움 |
| `MAX_TRADE_NOTIONAL_USD` | ❌ | 단일 트레이드 한도(USD) | `10000` |
| `MAX_TOTAL_NOTIONAL_USD` | ❌ | 총 미결제 노셔널 한도(USD) | `30000` |
| `DAILY_LOSS_LIMIT_KRW` | ❌ | 일일 실현손실 한도(KRW), 초과 시 신규 트레이드 중단 | `2000000` |
| `PORT` | ❌ | 리스닝 포트(PaaS가 주입; 미설정 시 8010) | PaaS 주입 |

> 전체 주석 포함 템플릿은 저장소의 `.env.production.example`을 참고하세요.

---

## 5. 플랫폼별 배포 (Per-platform)

모든 경로에서 시작 명령은 단일 워커 + `$PORT` 준수입니다.

### (a) Docker / docker-compose (Postgres 포함)

저장소의 `Dockerfile`은 `python:3.12-slim` 기반, 비특권 사용자(`appuser`)로 실행하며,
`/health`에 대한 stdlib HEALTHCHECK를 포함하고 다음으로 기동합니다.

```dockerfile
CMD ["sh","-c","uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8010} --workers 1"]
```

**단일 컨테이너(외부 DB 사용):**

```bash
# 이미지 빌드
docker build -t krw-watcher .

# .env(시크릿)를 런타임에 주입. .env는 이미지에 들어가지 않음(.dockerignore).
docker run -d --name krw-watcher -p 8010:8010 \
  --env-file .env \
  -e DEV_MODE=false -e ALLOWED_IPS='*' \
  -e DATABASE_URL='postgresql+asyncpg://USER:PASS@HOST:5432/DB' \
  krw-watcher
```

**docker-compose(번들 Postgres):** `docker-compose.yml`은 `app` + `postgres:16-alpine`(`db`)를 정의하고,
`DATABASE_URL=postgresql+asyncpg://krw:krw@db:5432/krw`, `ALLOWED_IPS=*`, `DEV_MODE=false`를 세팅합니다.
시크릿은 `env_file: .env`로 주입합니다.

```bash
# .env에 ANTHROPIC_API_KEY 등 시크릿을 채운 뒤
docker compose up -d --build

# 절대 스케일업 금지 (스케줄러/상태 중복)
# docker compose up --scale app=2   ← 하지 말 것
```

> 운영 DB 비밀번호는 compose 예시의 `krw:krw`에서 반드시 바꾸세요.

### (b) Render (`render.yaml`)

저장소의 `render.yaml`은 Docker 런타임 web 서비스 + 관리형 Postgres(`krw-db`)를 정의합니다.

- `numInstances: 1` (절대 상향 금지)
- `healthCheckPath: /health`
- `JWT_SECRET`은 `generateValue: true`로 Render가 한 번 생성해 고정
- `DEV_MODE=false`, `ALLOWED_IPS=*`, `BROKER=paper`, `ENABLE_LIVE_TRADING=false` 고정
- `DATABASE_URL`은 `krw-db`의 connectionString에서 자동 주입(init_db.py가 스킴 정규화)

단계:
1. 저장소를 GitHub에 푸시(`.env` 제외 확인).
2. Render → **New → Blueprint** → 저장소 선택 → `render.yaml` 감지.
3. 대시보드 **Environment** 탭에서 `sync: false`로 표시된 시크릿을 직접 입력:
   `ANTHROPIC_API_KEY`, `FRED_API_KEY`, `ADMIN_PASSWORD_HASH`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
   (필요 시 `BOK_ECOS_KEY`, `CRON_SECRET`, `DISABLE_AUTO_CYCLE`도 추가.)
4. Apply → 배포. `https://<service>.onrender.com/health`로 확인.

### (c) Railway (`railway.json` + Dockerfile)

Railway는 저장소의 `Dockerfile`을 그대로 사용합니다. `railway.json`이 없다면 프로젝트 루트에 아래를 생성하세요(`Dockerfile` 자동감지 시 생략 가능).

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": { "builder": "DOCKERFILE", "dockerfilePath": "Dockerfile" },
  "deploy": {
    "healthcheckPath": "/health",
    "healthcheckTimeout": 60,
    "numReplicas": 1,
    "restartPolicyType": "ON_FAILURE"
  }
}
```

단계:
1. Railway 프로젝트 생성 → 저장소 연결(또는 `railway up`).
2. **+ New → Database → PostgreSQL** 추가. Railway가 `DATABASE_URL`(`postgres://...`)을 주입 → init_db.py가 `postgresql+asyncpg://`로 정규화하고 sslmode를 처리.
3. **Variables**에 시크릿 설정: `ANTHROPIC_API_KEY`, `FRED_API_KEY`, `BOK_ECOS_KEY`,
   `JWT_SECRET`, `ADMIN_PASSWORD_HASH`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
   그리고 `DEV_MODE=false`, `ALLOWED_IPS=*`, `BROKER=paper`, `ENABLE_LIVE_TRADING=false`.
4. Railway는 `$PORT`를 주입 — Dockerfile CMD가 이미 `${PORT:-8010}`을 존중합니다.
5. **레플리카는 1로 고정**(`numReplicas: 1`). 배포 후 `https://<app>.up.railway.app/health` 확인.

### (d) Fly.io (`fly.toml` + `fly secrets set`)

저장소의 `fly.toml`은 `primary_region = "nrt"`(도쿄, 한국 근접), `internal_port = 8010`,
`force_https = true`, `min_machines_running = 1`(정확히 1대), `/health` 체크를 정의합니다.
비밀이 아닌 값(`DEV_MODE=false`, `ALLOWED_IPS=*`, `BROKER=paper`, `ENABLE_LIVE_TRADING=false`, `PORT=8010`)은 `[env]`에 들어 있습니다.

```bash
fly launch --no-deploy        # 기존 fly.toml 사용(앱 이름/리전 확인)

# 관리형 Postgres 연결 (예: Fly Postgres 또는 외부 Neon/Supabase)
# Fly Postgres 사용 시:
fly postgres create
fly postgres attach <pg-app-name>   # DATABASE_URL을 자동 시크릿으로 주입

# 시크릿 주입 (이미지에 절대 굽지 않음)
fly secrets set \
  ANTHROPIC_API_KEY=... FRED_API_KEY=... BOK_ECOS_KEY=... \
  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
  JWT_SECRET=... ADMIN_PASSWORD_HASH=... CRON_SECRET=...
# 외부 DB를 쓰면 DATABASE_URL=... 도 함께 set

fly deploy
fly scale count 1             # 반드시 1대로 (스케줄러/상태 중복 방지)
```

확인: `https://<app>.fly.dev/health`.

### (e) 자체 호스팅 VPS + Caddy (HTTPS)

앱은 `localhost:8010`에서 단일 워커로 돌리고, 그 앞에 Caddy를 두어 자동 HTTPS를 받습니다.
저장소의 `deploy/Caddyfile`을 도메인만 바꿔 사용하세요.

```
your-domain.example {
    encode gzip
    reverse_proxy localhost:8010
}
```

앱을 systemd 서비스로 띄우는 예시(`/etc/systemd/system/krw-watcher.service`):

```ini
[Unit]
Description=KRW-Watcher
After=network-online.target

[Service]
WorkingDirectory=/opt/krw-watcher
EnvironmentFile=/opt/krw-watcher/.env
# 단일 워커 — 절대 늘리지 말 것
ExecStart=/opt/krw-watcher/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8010 --workers 1
Restart=on-failure
User=krw

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now krw-watcher
sudo systemctl restart caddy        # Caddyfile 적용
```

> Caddy가 `X-Forwarded-For`/`X-Forwarded-Proto`를 자동 설정하고, 앱은 이를 신뢰해 실제 클라이언트 IP를 복원합니다.
> 프록시 뒤이므로 `.env`에 `ALLOWED_IPS=*`를 두어 읽기 엔드포인트가 도달 가능하게 하세요(쓰기 엔드포인트는 여전히 admin/`X-Cron-Secret` 필요).

---

## 6. Postgres 설정 + `DATABASE_URL`

- **로컬 개발**: 기본 SQLite(`sqlite+aiosqlite:///./krw_watcher.db`) — 별도 설정 불필요.
- **프로덕션**: 관리형 Postgres를 `DATABASE_URL`로 지정.

`backend/database/init_db.py`의 `_normalize_db_url()`이 관리형 Postgres 제공자(Railway/Render/Neon/Supabase)가 주는 형식을 자동 정규화합니다.

- `postgres://…` / `postgresql://…` → `postgresql+asyncpg://…`
- `?sslmode=require` / `?ssl=true` / `?sslmode=verify…` → `connect_args={"ssl": True}` (쿼리 스트링은 제거 — asyncpg는 libpq 쿼리 파라미터를 받지 않음)

권장 형식:

```
DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:5432/DB
```

> **테이블 자동 생성**: 별도 마이그레이션 단계가 없습니다. 앱 부팅 시 lifespan에서 `init_db()`가
> `Base.metadata.create_all`을 실행해 첫 기동에 테이블을 만듭니다. `asyncpg`는 `requirements.txt`에 이미 포함.

---

## 7. 비용 통제 (COST control)

AI 사이클은 호출마다 **Anthropic 토큰(유료)** 을 태웁니다. 스케줄(KST, `backend/scheduler/window_manager.py`):

- **데이터 수집**: 30분마다 — **토큰 없음(무료)**
- **AI 사이클**: 2시간마다 + 온쇼어 FX 세션 오픈(09:05)·클로즈(15:35) 추가 사이클 — **토큰 소모**
- **데일리 브리프**: 매일 `BRIEFING_HOUR_KST:10`(기본 08:10)에 텔레그램 발송 — **토큰 소모**

### 무료 데이터 전용 미러 — `DISABLE_AUTO_CYCLE=true`

이 값을 `true`로 두면 스케줄러는 **30분 데이터 수집만** 돌리고, **AI 사이클과 브리프 잡을 전혀 등록하지 않습니다.**
공개 읽기 전용 미러나 비용 통제에 적합합니다. 대시보드는 마지막으로 생성된 예측/데이터를 계속 보여줍니다.

### 수동 트리거

`DISABLE_AUTO_CYCLE=true`(또는 평소)에도 필요할 때만 사이클을 직접 돌릴 수 있습니다.

```bash
# admin 로그인 쿠키 사용
curl -b cookies.txt -X POST https://<host>/api/cycle

# 또는 외부 크론/웹훅 — X-Cron-Secret 헤더 (CRON_SECRET 설정 필요)
curl -X POST https://<host>/api/cycle \
  -H "X-Cron-Secret: $CRON_SECRET"

# 브리프 생성 + 텔레그램 발송
curl -X POST "https://<host>/api/briefing/generate?send=true" \
  -H "X-Cron-Secret: $CRON_SECRET"
```

`/api/cycle`은 이미 사이클이 도는 중이면 `{"status":"busy"}`를, 시작하면 `{"status":"started"}`를 반환합니다(백그라운드 실행).

> 비용 절약 패턴: `DISABLE_AUTO_CYCLE=true`로 무료 미러를 운영하다가, 외부 스케줄러(예: GitHub Actions cron, cron-job.org)가 하루 한두 번 `X-Cron-Secret`으로 `/api/cycle`을 호출하도록 구성하면 토큰 사용을 정밀하게 통제할 수 있습니다.

---

## 8. 텔레그램 설정 (Telegram)

데일리 브리프와 실시간 예측 변경 알림을 텔레그램으로 받습니다.

1. **봇 생성**: 텔레그램에서 `@BotFather`에게 `/newbot` → 발급된 토큰을 `TELEGRAM_BOT_TOKEN`에 설정.
2. **chat id 확인**: 봇과 대화를 시작한 뒤 `https://api.telegram.org/bot<TOKEN>/getUpdates`를 열어 `chat.id`를 확인 → `TELEGRAM_CHAT_ID`에 설정(여러 명이면 CSV).
3. (선택) `BRIEFING_HOUR_KST`로 발송 시각 조정(기본 8시 → 매일 08:10 KST 생성·발송).

전달 방식:
- **데일리 브리프**: 스케줄러가 매일 `BRIEFING_HOUR_KST:10`에 `generate_and_send(send=True)`로 생성·발송. `POST /api/briefing/generate`로 수동 발송도 가능.
- **실시간 알림**: AI 사이클에서 발행 예측이 의미 있게 바뀌면(`changed_h`) 해당 시점에 텔레그램으로 푸시.

> 텔레그램 변수를 비워 두면 발송 단계만 조용히 건너뜁니다(앱은 정상 동작).

---

## 9. 업데이트 / 재배포 / 검증

### 재배포

- **Docker/compose**: `docker compose up -d --build` (또는 `docker build` 후 `docker run`).
- **Render**: 기본 브랜치에 푸시하면 자동 재배포(Blueprint).
- **Railway**: 푸시 시 자동 빌드, 또는 `railway up`.
- **Fly.io**: `fly deploy` 후 `fly scale count 1` 확인.
- **VPS(systemd)**: 코드 갱신 후 `pip install -r requirements.txt` → `sudo systemctl restart krw-watcher`.

> 재배포 시에도 **단일 인스턴스/단일 워커**를 반드시 유지하고, `.env`/시크릿이 이미지·커밋에 들어가지 않았는지 확인하세요.

### 검증 (Verify)

1. **헬스체크** — `GET /health`가 200 JSON을 반환:

   ```bash
   curl -s https://<host>/health
   ```

   응답에 `status: "ok"`, `model`, `broker`, `live_trading`, `spot`, `data_last_collected`, `agent_count`가 포함됩니다.
   - `agent_count`가 위원회 규모와 일치하는지 확인.
   - `data_last_collected`가 채워지면 30분 데이터 스윕이 동작 중.
   - 공개 배포라면 `live_trading: false`, `broker: "paper"`인지 확인.

2. **대시보드** — 브라우저로 `https://<host>/` 접속 → 예측/시그널/에이전트/뉴스가 렌더링되는지 확인. `/docs`에서 API도 점검 가능.

3. **보호 라우트** — 비로그인 상태에서 `POST /api/cycle` 호출 시 401(`DEV_MODE=false`, `ALLOWED_IPS=*` 기준)이 나오는지 확인. admin 로그인 또는 `X-Cron-Secret`으로는 통과해야 함.

4. **(선택) 사이클 1회** — admin 로그인 또는 `X-Cron-Secret`으로 `POST /api/cycle` → `{"status":"started"}` 확인 후, 잠시 뒤 `GET /api/forecast`에 결과가 반영되는지 확인.
