# Oracle Cloud Always Free VM 배포 가이드

Render(유료, 정지됨)를 대체하는 **영구 무료 + 상시 구동** 배포. 구조:

```
로컬 PC ── git push ──► GitHub (private repo)
                            │ git pull (update.sh / cron)
                            ▼
              Oracle 무료 VM (Ubuntu, ARM)
              docker compose ─► krw-watcher :8010
              SQLite → deploy/oracle/data/ (영속)
```

---

## 1. Oracle Cloud 계정 생성 (1회)

1. https://signup.oraclecloud.com 에서 가입 — **Home Region: South Korea Central (Seoul)** 권장 (변경 불가이니 신중히).
2. 카드 인증이 필요하지만 **Always Free 리소스만 쓰면 과금되지 않습니다** (Pay As You Go로 업그레이드하지 않는 한 청구 없음).

## 2. VM 생성

Console → Compute → Instances → **Create Instance**:

| 항목 | 값 |
|---|---|
| Image | **Ubuntu 24.04** (aarch64) |
| Shape | **VM.Standard.A1.Flex** — 2 OCPU / 12GB면 충분 (무료 한도: 총 4 OCPU / 24GB) |
| SSH keys | 공개키 등록 (없으면 `ssh-keygen`으로 생성) |
| Public IP | Assign a public IPv4 address 체크 |

> **"Out of capacity" 에러가 나면**: 같은 리전의 다른 Availability Domain을 선택하거나, OCPU를 1~2로 줄이거나, 몇 시간 뒤 재시도. (서울 리전 A1은 수요가 많아 종종 발생)

## 3. 네트워크 포트 열기 (OCI 콘솔)

Instance 상세 → Virtual Cloud Network → Security Lists → Default Security List → **Add Ingress Rule**:

- Source CIDR: `0.0.0.0/0`
- IP Protocol: TCP, Destination Port: `8010`

(VM 내부 iptables는 `setup-vm.sh`가 열어줍니다 — 둘 다 필요합니다.)

## 4. GitHub repo 준비 (로컬 PC에서)

아직 원격이 없으므로 private repo를 만들어 push:

```powershell
gh repo create krw-watcher --private --source . --push
```

## 5. VM 초기 세팅

```bash
ssh ubuntu@<VM_공인_IP>

# private repo clone — 인증은 GitHub CLI가 제일 간단
sudo apt-get update && sudo apt-get install -y gh git
gh auth login          # 브라우저 디바이스 코드 방식
gh repo clone <깃허브아이디>/krw-watcher
cd krw-watcher

bash deploy/oracle/setup-vm.sh
```

`.env`를 repo 루트에 생성 (`.env.example` 참고). 프로덕션 필수 항목:

```bash
nano .env
```

```ini
ANTHROPIC_API_KEY=sk-ant-...
FRED_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
BROKER=paper
ENABLE_LIVE_TRADING=false
DEV_MODE=false
ALLOWED_IPS=*
JWT_SECRET=<32자 이상 랜덤>
ADMIN_PASSWORD_HASH=<python setup.py 로 생성>
# DATABASE_URL은 compose가 SQLite(/data)로 주입하므로 설정 불필요
```

## 6. 기동 & 확인

```bash
# docker 그룹 반영을 위해 재로그인 후
cd ~/krw-watcher/deploy/oracle
docker compose up -d --build

curl http://localhost:8010/health          # {"status":"ok"} 확인
docker compose logs -f app                 # 스케줄러/사이클 로그 확인
```

브라우저: `http://<VM_공인_IP>:8010`

## 7. 업데이트 (GitHub 기반 배포)

로컬에서 `git push` → VM에서:

```bash
bash ~/krw-watcher/deploy/oracle/update.sh
```

자동 배포를 원하면 VM에 cron 등록 (5분마다 새 커밋 감지 시 자동 재배포):

```bash
crontab -e
# 추가:
*/5 * * * * cd /home/ubuntu/krw-watcher && bash deploy/oracle/update.sh >> /home/ubuntu/deploy.log 2>&1
```

## 8. (선택) HTTPS + 도메인

`http://IP:8010`으로 충분하면 생략. 필요해지면:

- **Cloudflare Tunnel** (본인 도메인이 Cloudflare에 있을 때): 포트 노출 없이 HTTPS URL
- **Caddy + DuckDNS**: 무료 서브도메인 + 자동 TLS

## 운영 메모

- **인스턴스 회수 방지**: Always Free A1은 CPU 사용률이 장기간 극히 낮으면 회수될 수 있음 — 이 앱은 30분마다 데이터 수집이 돌아서 해당 없음.
- **백업**: SQLite는 `deploy/oracle/data/krw_watcher.db` 하나. 주기적으로 `scp`로 내려받거나 cron으로 복사 보관.
- **단일 인스턴스 원칙**: 인메모리 상태 + APScheduler 때문에 컨테이너/워커를 2개 이상 띄우지 말 것 (render.yaml 상단 주석 참고).
