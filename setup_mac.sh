#!/bin/bash
echo "================================================"
echo " KODEX ETF Agent — Mac 설치 스크립트"
echo "================================================"
echo ""

# Python 확인
if command -v python3 &>/dev/null; then
    echo "✅ Python 이미 설치됨: $(python3 --version)"
else
    echo "[1/3] Python 설치 중..."
    # Homebrew 있으면 brew로 설치
    if command -v brew &>/dev/null; then
        brew install python3
    else
        echo "❌ Homebrew가 없습니다. 아래 중 하나를 선택하세요:"
        echo ""
        echo "  방법1 (Homebrew 먼저 설치):"
        echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo "    brew install python3"
        echo ""
        echo "  방법2 (직접 다운로드):"
        echo "    https://www.python.org/downloads/ 에서 macOS 버전 다운로드"
        echo ""
        echo "Python 설치 후 이 스크립트를 다시 실행하세요."
        exit 1
    fi
fi
echo ""

# pip 업그레이드
echo "[2/3] pip 업그레이드..."
python3 -m pip install --upgrade pip --quiet
echo "✅ pip 업그레이드 완료"
echo ""

# 패키지 설치
echo "[3/3] 패키지 설치 중... (5~10분 소요)"
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "❌ 패키지 설치 실패"
    exit 1
fi
echo ""
echo "✅ 설치 완료!"
echo ""
echo "================================================"
echo " 실행: 터미널에서 아래 명령어 입력"
echo " sh run_mac.sh"
echo "================================================"
