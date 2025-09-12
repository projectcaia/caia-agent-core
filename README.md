# CaiaAgent Core v3.0.1

CaiaAgent Core는 n8n 워크플로우를 관리하고 오케스트레이션하는 FastAPI 기반 서비스입니다.

## 주요 기능

- **n8n 워크플로우 완전 관리**: 생성, 조회, 수정, 삭제, 활성화/비활성화, 실행
- **Bootstrap 자동화**: 4개의 표준 워크플로우 자동 배포
- **메모리 관리**: 컨텍스트 기반 의사결정
- **보안 인증**: API 키 및 BasicAuth 지원

## API 엔드포인트

### Core 엔드포인트
- `GET /health` - 헬스체크
- `GET /status` - 상태 확인
- `POST /orchestrate` - 오케스트레이션
- `POST /report` - 리포트 저장

### n8n 관리 엔드포인트
- `POST /n8n/bootstrap` - 표준 워크플로우 4종 자동 생성
- `GET /n8n/workflows` - 워크플로우 목록 조회
- `GET /n8n/workflows/{id}` - 워크플로우 상세 조회
- `POST /n8n/workflows` - 워크플로우 생성
- `PUT /n8n/workflows/{id}` - 워크플로우 수정
- `DELETE /n8n/workflows/{id}` - 워크플로우 삭제
- `POST /n8n/workflows/{id}/activate` - 워크플로우 활성화
- `POST /n8n/workflows/{id}/deactivate` - 워크플로우 비활성화
- `POST /n8n/workflows/{id}/test` - 워크플로우 테스트 실행
- `GET /n8n/executions` - 실행 이력 조회

## 환경변수 설정

### 필수 환경변수 (Core)

```bash
# Core 설정
CAIA_AGENT_HEALTH_URL="https://caia-agent-core-production.up.railway.app/health"
CAIA_AGENT_KEY="G2k7n9q4YxW3t8P5G2k7n9q4Yx"
DEFAULT_WEBHOOK_PATH="caia_core_loop"
LOG_LEVEL="info"

# n8n 연동 (필수)
N8N_API_URL="https://caia-agent-production.up.railway.app/api/v1"
N8N_API_KEY="<n8n에서 생성한 API 키>"

# n8n 크레덴셜 이름 매핑
N8N_CRED_GCAL="Google Calendar account"
N8N_CRED_GDRIVE="gdrive_main"
N8N_CRED_GMAIL="Gmail account"
N8N_CRED_OPENAI="OpenAi account 2"
N8N_CRED_TELEGRAM="Telegram account 2"

# 워크플로우 설정
N8N_FORWARD_TO="flyartnam@gmail.com"
N8N_TG_CHAT_ID="8046036996"

# Telegram 설정
TELEGRAM_BOT_TOKEN="<텔레그램 봇 토큰>"
TELEGRAM_CHAT_ID="8046036996"
```

### n8n 인스턴스 환경변수

```bash
# n8n 기본 설정
N8N_HOST="caia-agent-production.up.railway.app"
N8N_PROTOCOL="https"
N8N_EDITOR_BASE_URL="https://caia-agent-production.up.railway.app"
N8N_ENCRYPTION_KEY="caia-agent-n8n-20250607-caia-birth-day-20250902-n8n"

# n8n 실행 설정
N8N_RUNNERS_ENABLED="true"
N8N_TRUSTED_PROXIES="0.0.0.0/0,::/0"
N8N_PROXY_HOPS="1"
N8N_LISTEN_ADDRESS="0.0.0.0"
N8N_BLOCK_ENV_ACCESS_IN_NODE="false"
N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS="true"

# 데이터베이스 설정
DB_TYPE="postgresdb"
DB_POSTGRESDB_DATABASE="railway"
DB_POSTGRESDB_HOST="postgres.railway.internal"
DB_POSTGRESDB_USER="postgres"
DB_POSTGRESDB_PASSWORD="<Railway Postgres 비밀번호>"
DB_POSTGRESDB_PORT="5432"

# 크레덴셜 및 워크플로우 설정 (Core와 동일)
N8N_CRED_GCAL="Google Calendar account"
N8N_CRED_GDRIVE="gdrive_main"
N8N_CRED_GMAIL="Gmail account"
N8N_CRED_OPENAI="OpenAi account 2"
N8N_CRED_TELEGRAM="Telegram account 2"
N8N_FORWARD_TO="flyartnam@gmail.com"
N8N_TG_CHAT_ID="8046036996"
TELEGRAM_BOT_TOKEN="<텔레그램 봇 토큰>"
TELEGRAM_CHAT_ID="8046036996"
```

## n8n API 키 생성 방법

1. n8n 웹 UI에 로그인
2. Settings → API Settings로 이동
3. "Create an API Key" 클릭
4. 생성된 API 키를 복사하여 `N8N_API_KEY` 환경변수에 설정

## Bootstrap 워크플로우

`/n8n/bootstrap` 엔드포인트를 호출하면 다음 4개의 워크플로우가 자동 생성됩니다:

1. **mail-digest**: Gmail 일일 다이제스트 (매일 09:00)
2. **tg-to-gmail**: Telegram → Gmail 메시지 포워딩
3. **failure-guard**: 실패 감지 및 복구
4. **heartbeat**: Core 헬스체크 (매일 08:55)

## 배포

### Railway 배포

1. Railway 프로젝트에서 서비스 생성
2. GitHub 저장소 연결
3. 환경변수 설정
4. 자동 배포 활성화

### Docker 실행

```bash
# 빌드
docker build -t caia-agent-core .

# 실행
docker run -p 8080:8080 \
  -e N8N_API_URL="https://your-n8n.com/api/v1" \
  -e N8N_API_KEY="your-api-key" \
  caia-agent-core
```

## 인증 방식

### X-N8N-API-KEY 헤더 (권장)
- n8n API 키 사용 시 자동으로 `X-N8N-API-KEY` 헤더 사용
- `Authorization: Bearer` 방식은 제거됨

### BasicAuth (옵션)
- `N8N_BASIC_AUTH_USER`와 `N8N_BASIC_AUTH_PASSWORD` 설정 시 사용
- API 키보다 우선순위 높음

## 문제 해결

### 401 Unauthorized 에러
- n8n API 키가 올바른지 확인
- `N8N_API_URL`이 `/api/v1`로 끝나는지 확인
- n8n 인스턴스가 API 키 인증을 지원하는지 확인

### 워크플로우 실행 실패
- 일부 n8n 버전은 `/run` 엔드포인트를 지원하지 않음
- 이 경우 "Use UI 'Execute Workflow' instead" 메시지 표시
- n8n 웹 UI에서 직접 실행 필요

### 크레덴셜 누락
- Bootstrap 결과에서 `missingCredentials` 확인
- n8n UI에서 해당 크레덴셜 생성 필요

## 개발

```bash
# 의존성 설치
pip install -r requirements.txt

# 개발 서버 실행
uvicorn main:app --reload --port 8080
```

## 라이선스

Private - CaiaAgent Project