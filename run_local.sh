#!/bin/bash
# CaiaAgent Core 로컬 실행 스크립트

echo "=========================================="
echo "CaiaAgent Core v3.0.1 - Local Development"
echo "=========================================="

# .env 파일이 있으면 환경변수로 로드
if [ -f .env ]; then
    echo "Loading environment variables from .env file..."
    export $(cat .env | grep -v '^#' | xargs)
else
    echo "Warning: .env file not found!"
    echo "Please create .env file from .env.example:"
    echo "  cp .env.example .env"
    echo ""
fi

# Python 가상환경 활성화 (있으면)
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

# 필요한 패키지 설치
echo "Installing/updating dependencies..."
pip install -r requirements.txt

# 서버 시작
echo ""
echo "Starting CaiaAgent Core..."
echo "=========================================="
echo "API Documentation: http://localhost:8080/docs"
echo "Health Check: http://localhost:8080/health"
echo "=========================================="
echo ""

# uvicorn 실행 (reload 옵션으로 개발 모드)
uvicorn main:app --reload --host 0.0.0.0 --port 8080 --log-level info