@echo off
chcp 65001 > nul
cd /d "%~dp0"

:: .env 파일이 있으면 환경변수 로드
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
    )
)

:: Streamlit 실행
echo 🚀 KODEX ETF 마케팅 효과 측정 Agent 시작...
echo    브라우저: http://localhost:8501
echo    종료: Ctrl+C
echo.
python -m streamlit run app.py --server.port 8501 --browser.gatherUsageStats false
