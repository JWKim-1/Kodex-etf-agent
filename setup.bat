@echo off
chcp 65001 > nul
echo ================================================
echo  KODEX ETF 마케팅 효과 측정 Agent — 설치 스크립트
echo ================================================
echo.

:: Python 확인
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [1/3] Python 설치 중 (winget)...
    winget install Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo.
        echo ❌ Python 자동 설치 실패.
        echo    수동 설치: https://www.python.org/downloads/
        echo    설치 후 이 스크립트를 다시 실행하세요.
        pause
        exit /b 1
    )
    echo ✅ Python 설치 완료
    :: PATH 갱신을 위해 재시작 필요
    echo.
    echo ⚠️  Python 설치 후 PATH 적용을 위해 새 터미널에서 다시 실행하세요.
    pause
    exit /b 0
) else (
    echo ✅ Python 이미 설치됨:
    python --version
)
echo.

:: pip 업그레이드
echo [2/3] pip 업그레이드...
python -m pip install --upgrade pip --quiet
echo ✅ pip 업그레이드 완료
echo.

:: 패키지 설치
echo [3/3] 패키지 설치 중...
cd /d "%~dp0"
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo ❌ 패키지 설치 실패. requirements.txt를 확인하세요.
    pause
    exit /b 1
)
echo.
echo ✅ 모든 패키지 설치 완료
echo.
echo ================================================
echo  실행 방법: run.bat 또는 아래 명령 실행
echo  streamlit run app.py
echo ================================================
echo.
pause
