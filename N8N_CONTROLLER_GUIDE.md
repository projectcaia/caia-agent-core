# n8n Controller Guide for CaiaAgent

## 🎯 목적

CaiaAgent가 n8n 서비스를 상시 실행하지 않고, **필요할 때만 Railway API를 통해 자동으로 시작하고 중단**하여 비용을 절감합니다.

## 🚀 주요 기능

### 자동 생명주기 관리
- n8n 워크플로우 실행 요청 시 자동으로 서비스 시작
- 워크플로우 실행 완료 후 자동으로 서비스 중단
- 대기 상태에서는 **비용 0원** (Railway는 중단된 서비스에 과금하지 않음)

### Railway GraphQL API 통합
- Railway 플랫폼의 GraphQL API를 통한 서비스 제어
- `serviceStart` / `serviceStop` mutation 사용
- 안정적인 에러 처리 및 재시도 로직

### 배치 작업 지원
- 여러 워크플로우를 연속 실행할 때 n8n을 한 번만 시작/중단
- `N8NBatchOperation` 컨텍스트 매니저 제공

## 📋 설정 방법

### 1. 환경 변수 설정

`.env` 파일에 다음 변수들을 설정하세요:

```env
# Railway API 설정 (필수)
RAILWAY_API_TOKEN="railway_xxxxxxxxxxxxxxxxx"  # Railway 대시보드에서 생성
N8N_SERVICE_ID="abcd1234"  # Railway에서 n8n 서비스 ID

# n8n 서비스 설정
N8N_HOST="caia-agent-production.up.railway.app"
N8N_PROTOCOL="https"
N8N_STARTUP_WAIT="10"  # n8n 시작 후 대기 시간 (초)

# n8n API 인증 (기존 설정 유지)
N8N_API_URL="https://caia-agent-production.up.railway.app/api/v1"
N8N_API_KEY="your-n8n-api-key"
```

### 2. Railway API Token 생성

1. [Railway 대시보드](https://railway.app/account/tokens)에 로그인
2. Account Settings → Tokens로 이동
3. "New Token" 클릭하여 API 토큰 생성
4. 생성된 토큰을 `RAILWAY_API_TOKEN`에 설정

### 3. n8n Service ID 찾기

1. Railway 프로젝트 대시보드 열기
2. n8n 서비스 클릭
3. Settings 탭에서 Service ID 확인
4. 해당 ID를 `N8N_SERVICE_ID`에 설정

## 🔧 사용 방법

### 테스트 엔드포인트

#### n8n 서비스 시작
```bash
curl -X GET http://localhost:8000/test/n8n/start
```

#### n8n 서비스 중단
```bash
curl -X GET http://localhost:8000/test/n8n/stop
```

#### 서비스 상태 확인
```bash
curl -X GET http://localhost:8000/test/n8n/status
```

#### 워크플로우 실행 (자동 제어)
```bash
curl -X POST http://localhost:8000/test/n8n/workflow/{workflow_id} \
  -H "Content-Type: application/json" \
  -d '{"data": "your payload"}'
```

### 프로덕션 엔드포인트

#### Webhook 프록시 (자동 제어)
외부 서비스가 n8n webhook을 호출할 때 사용:

```bash
curl -X POST http://localhost:8000/webhook/{workflow_id} \
  -H "Content-Type: application/json" \
  -d '{"your": "webhook payload"}'
```

#### 배치 워크플로우 실행
여러 워크플로우를 한 번에 실행:

```bash
curl -X POST http://localhost:8000/batch/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "workflows": [
      {"id": "workflow1", "payload": {"data": "test1"}},
      {"id": "workflow2", "payload": {"data": "test2"}}
    ]
  }'
```

## 🐍 Python 코드 예제

### 단일 워크플로우 실행
```python
from n8n_controller import use_n8n_workflow

# 자동으로 n8n 시작 → 실행 → 중단
result = use_n8n_workflow(
    workflow_id="your-workflow-id",
    payload={"message": "Hello World"}
)
```

### 배치 작업
```python
from n8n_controller import N8NBatchOperation, use_n8n_workflow

# n8n을 한 번만 시작하고 여러 작업 실행
with N8NBatchOperation():
    result1 = use_n8n_workflow("workflow1", data1, keep_alive=True)
    result2 = use_n8n_workflow("workflow2", data2, keep_alive=True)
    result3 = use_n8n_workflow("workflow3", data3, keep_alive=True)
# 컨텍스트 종료 시 자동으로 n8n 중단
```

## 📊 로그 및 모니터링

### 콘솔 로그 예시
```
[INFO] 🚀 Starting n8n service via Railway API...
[INFO] n8n service start initiated, waiting 10 seconds for initialization...
[INFO] ✅ n8n service is healthy and ready!
[INFO] 📋 Executing n8n workflow: mail-digest
[INFO] ✅ Workflow executed successfully: mail-digest
[INFO] 🛑 Stopping n8n service via Railway API...
[INFO] ✅ n8n service stopped successfully
```

### Railway 대시보드
- Railway 대시보드에서 실시간 서비스 상태 확인 가능
- Metrics 탭에서 CPU/메모리 사용량 모니터링
- Logs 탭에서 n8n 로그 확인

## ⚠️ 주의사항

1. **첫 실행 지연**: n8n 서비스 시작에 8-15초 소요
2. **동시 요청 처리**: 여러 요청이 동시에 올 경우 첫 요청만 n8n을 시작
3. **에러 복구**: n8n 중단 실패 시에도 워크플로우 결과는 반환
4. **타임아웃**: 워크플로우 실행 타임아웃은 2분으로 설정

## 💰 비용 절감 효과

### Before (상시 실행)
- 24시간 x 30일 = 720시간/월
- 예상 비용: ~$5-10/월

### After (필요시만 실행)
- 일 평균 30분 사용 가정
- 0.5시간 x 30일 = 15시간/월
- 예상 비용: ~$0.1-0.3/월
- **절감률: 약 95-98%**

## 🔍 문제 해결

### Railway API Token이 유효하지 않음
```
Error: Railway API error: Unauthorized
```
→ Railway 대시보드에서 새 토큰 생성 후 재설정

### Service ID를 찾을 수 없음
```
Error: N8N_SERVICE_ID is not configured
```
→ Railway 프로젝트에서 정확한 Service ID 확인

### n8n이 시작되지 않음
```
Error: n8n health check failed
```
→ Railway 대시보드에서 n8n 서비스 로그 확인

## 📚 추가 리소스

- [Railway GraphQL API 문서](https://docs.railway.app/reference/graphql-api)
- [n8n Webhook 문서](https://docs.n8n.io/core-nodes/n8n-nodes-base.webhook/)
- [CaiaAgent 저장소](https://github.com/projectcaia/caia-agent-core)

## 🤝 기여하기

이슈 발견 시 [GitHub Issues](https://github.com/projectcaia/caia-agent-core/issues)에 제보해주세요.