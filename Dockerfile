FROM python:3.11-slim

# 작업 디렉토리 설정
WORKDIR /app

# requirements.txt 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 프로젝트 전체 복사 (기존 *.py → 전체 복사로 변경)
COPY . .

# 환경변수 설정
ENV PORT=8080
EXPOSE ${PORT}

# Uvicorn으로 애플리케이션 실행
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
