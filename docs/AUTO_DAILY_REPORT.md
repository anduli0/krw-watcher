# 매일 자동 예측 리포트 (Daily report guarantee)

2026-07-07 추가. 목표: **매일 정확히 한 번, 예측 리포트(AI 사이클 + 데일리 브리프)가 자동 생성**되도록 보장.
기존 문제: 예약 사이클(2시간마다)이 `DISABLE_AUTO_CYCLE` 또는 컨테이너 재시작으로 건너뛰면 그날 리포트가 비어 있었음.

## 동작 (3중 안전장치, 모두 같은 멱등 체크로 수렴)

1. **30분 데이터 스윕이 자가치유** — 스윕은 DATA-ONLY 모드에서도 항상 돌기 때문에,
   `DAILY_REPORT_HOUR_KST`(기본 08시) 이후 첫 스윕이 그날 리포트가 없으면 생성한다.
   → **현재 상시가동(Render starter) 배포에서 외부 인프라 없이 즉시 작동.**
2. **전용 스케줄러 크론** — 매일 `DAILY_REPORT_HOUR_KST:15` KST에 한 번 더 호출(빠른 전달).
   `DISABLE_AUTO_CYCLE` 조기 return 앞에 등록되어 DATA-ONLY 모드에서도 실행됨.
3. **외부 크론 엔드포인트** — `POST /api/report/daily` (PC 무관, cron-job.org/Render 크론용).

멱등성: KST 달력 날짜 기준. 그날 **완료된 run + 한국어 브리프**가 모두 있으면 "already_done"으로 무동작.
하루 재시도 상한 `MAX_ATTEMPTS_PER_DAY=4` → 일시적 실패는 다음 스윕에서 재시도하되 토큰 폭주는 없음.
비용: 이 경로에서 하루 최대 1개 리포트.

## 설정 (backend/config.py, 환경변수로 오버라이드)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DISABLE_DAILY_REPORT` | `false` | `true`면 매일 보장 끔(수동/예약 사이클만) |
| `DAILY_REPORT_HOUR_KST` | `8` | 리포트가 생성될 수 있는 가장 이른 KST 시각 |
| `CRON_SECRET` | `""` | 설정 시 `/api/cycle`·`/api/report/daily`에 `X-Cron-Secret` 헤더 필요 |

## 엔드포인트

- `POST /api/report/daily` — 오늘 리포트를 보장 생성(없으면 생성). 동기 실행, 상태 JSON 반환.
  `CRON_SECRET` 설정 시 헤더 필요.
- `GET  /api/report/daily/status` — 오늘 리포트 유무 `{date, run, brief, complete}` (인증 불필요).
- `POST /api/cycle` — 즉시 AI 사이클(비동기). `CRON_SECRET` 설정 시 헤더 필요.

## 외부 크론 설정 (선택 — PC 무관 belt-and-suspenders)

Render 대시보드에서 `CRON_SECRET`을 설정한 뒤, cron-job.org 등에서 매일 1회:

```
POST https://krw-watcher.onrender.com/api/report/daily
Header: X-Cron-Secret: <CRON_SECRET 값>
```

> 참고: 30분 스윕 자가치유가 이미 상시가동 배포에서 리포트를 보장하므로 외부 크론은 이중 안전장치다.
> 리포트 생성은 유효한 `ANTHROPIC_API_KEY`(Render 환경변수)가 있어야 실제 AI 사이클을 돈다.
