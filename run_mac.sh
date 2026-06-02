#!/bin/bash
cd "$(dirname "$0")"

# .env 파일 있으면 환경변수 로드
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "🚀 KODEX ETF 마케팅 효과 측정 Agent 시작..."
echo "   브라우저: http://localhost:8501"
echo "   종료: Ctrl+C"
echo ""
python3 -m streamlit run app.py --server.port 8501 --browser.gatherUsageStats false
