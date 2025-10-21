# CaiaAgent Core v3.0.1 트러블슈팅 가이드

## 🔴 현재 발생한 문제 및 해결책

### 1. 404 Not Found 에러 (해결됨 ✅)
**문제**: 루트 경로(/)에 접근 시 404 에러 발생
```
INFO: 100.64.0.2:19422 - "GET / HTTP/1.1" 404 찾을 수 없음
```

**해결책**: 
- 루트 엔드포인트(/)를 main.py에 추가
- 이제 `/` 경로에서 API 정보와 상태를 확인할 수 있음

### 2. n8n 연동 비활성화 (해결 필요 ⚠️)
**문제**: n8n 기능이 작동하지 않음
```
ERROR:main:Missing N8N_API_URL
INFO:main:n8n configured: False
WARNING:main:N8N_API_URL not set - n8n features disabled
```

**해결책**:
Railway 환경변수에 다음 설정 추가 필요:

```bash
# n8n 연동 필수 설정
N8N_API_URL=https://caia-agent-production.up.railway.app/api/v1
N8N_API_KEY=<n8n에서 생성한 API 키>

# 크레덴셜 이름 매핑
N8N_CRED_GCAL=Google Calendar account
N8N_CRED_GDRIVE=gdrive_main
N8N_CRED_GMAIL=Gmail account
N8N_CRED_OPENAI=OpenAi account 2
N8N_CRED_TELEGRAM=Telegram account 2

# 워크플로우 설정
N8N_FORWARD_TO=flyartnam@gmail.com
N8N_TG_CHAT_ID=8046036996

# Telegram 설정 (선택사항)
TELEGRAM_BOT_TOKEN=<텔레그램 봇 토큰>
TELEGRAM_CHAT_ID=8046036996
```

## 📊 상태 확인 방법

### 1. 헬스체크
```bash
curl https://your-domain/health
```

### 2. 전체 상태 확인
```bash
curl https://your-domain/status
```

### 3. API 문서
- Swagger UI: `https://your-domain/docs`
- OpenAPI Schema: `https://your-domain/openapi.json`

## 🔧 n8n API 키 생성 방법

1. n8n 웹 UI에 로그인
2. **Settings** → **API Settings**로 이동
3. **"Create an API Key"** 클릭
4. 생성된 API 키를 복사
5. Railway 환경변수에 `N8N_API_KEY`로 설정

## ✅ 정상 작동 확인 체크리스트

- [ ] `/health` 엔드포인트가 `{"ok": true}` 반환
- [ ] `/status`에서 `n8n_configured: true` 확인
- [ ] `/n8n/workflows` 호출 시 워크플로우 목록 조회 성공
- [ ] `/n8n/bootstrap` 실행 시 4개의 표준 워크플로우 생성 성공

## 🚀 빠른 테스트

### 로컬 테스트
```bash
# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일 편집하여 실제 값 입력

# 서버 시작
./run_local.sh

# API 테스트
python test_api.py
```

### Docker 테스트
```bash
# 빌드
docker build -t caia-agent-core .

# 실행 (환경변수 포함)
docker run -p 8080:8080 \
  -e N8N_API_URL="https://caia-agent-production.up.railway.app/api/v1" \
  -e N8N_API_KEY="your-api-key" \
  caia-agent-core
```

## 📝 주요 변경 사항 (v3.0.1)

1. **루트 엔드포인트 추가**: API 정보 및 상태를 한눈에 확인
2. **향상된 로깅**: 시작 시 환경변수 상태 명확히 표시
3. **테스트 스크립트 추가**: `test_api.py`로 쉽게 API 테스트
4. **Railway 설정 파일**: `railway.toml` 추가로 자동 헬스체크

## 🆘 추가 지원

문제가 지속되면 다음을 확인하세요:

1. **n8n 인스턴스 상태**: n8n이 실제로 실행 중인지 확인
2. **API 키 권한**: 생성한 API 키가 충분한 권한을 가지고 있는지 확인
3. **네트워크 연결**: Railway 서비스 간 내부 통신이 가능한지 확인
4. **로그 확인**: Railway 대시보드에서 전체 로그 확인