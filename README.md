# KODEX ETF 마케팅 효과 측정 AI Agent

삼성증권 채널 마케팅 활동이 Kodex ETF 순매수에 미친 영향을 이중차분법(DiD)으로 정량 측정하는 Agent.

## 설치

```bash
pip install -r requirements.txt
```

### Selenium 크롬 드라이버
첫 실행 시 `webdriver-manager`가 ChromeDriver를 자동 설치합니다.  
Chrome 브라우저가 설치되어 있어야 합니다.

## API 키 설정

```bash
cp .env.example .env
# .env 파일을 열고 보유한 키 입력
```

| 키 | 필수 | 없을 때 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 권장 | 키워드 기반 ETF 감지로 대체 |
| `YOUTUBE_API_KEY` | 선택 | YouTube RSS 피드로 대체 |
| `NAVER_CLIENT_ID/SECRET` | 선택 | 구글 뉴스 RSS로 대체 |

## 실행

```bash
streamlit run app.py
```

브라우저가 자동으로 `http://localhost:8501` 을 엽니다.

## 매주 사용 방법

1. 멘토님께 받은 최신 주간 순매수 데이터를 엑셀 시트로 추가
2. 앱에 엑셀 파일 업로드
3. 현재 주 시트 선택
4. **분석 시작** 클릭
5. HTML 리포트 다운로드

## 파일 구조

```
kodex agent/
├── app.py           # Streamlit 메인 앱
├── collector.py     # 11개 마케팅 채널 수집 모듈
├── analyzer.py      # DiD 분석 엔진
├── report.py        # 주간 리포트 + HTML 내보내기
├── requirements.txt
├── .env.example
└── ETF 순매수 데이터_260529.xlsx   # 베이스라인용 과거 데이터
```

## 분석 로직

```
Step 1. 11개 채널 마케팅 데이터 수집 (실패 시 이유 분류 보고)
Step 2. LLM(Claude)으로 대상 ETF 종목 자동 추출
Step 3. 직전 4주 이동평균 베이스라인 계산
Step 4. LP 노이즈 감지 (±2σ 기준)
Step 5. DiD = Kodex 변화율 - 비교군(TIGER/ACE) 평균 변화율
```

### DiD 판정 기준

| DiD 수치 | 판정 |
|---|---|
| +50%p 이상 | 🟢 마케팅 효과 강함 |
| +20~+50%p | 🟡 마케팅 효과 있음 |
| -20~+20%p | ⚪ 효과 불분명 |
| -20%p 미만 | 🔴 효과 없음 또는 역효과 |

## 채널별 수집 결과

| 채널 | 예상 결과 |
|---|---|
| 삼성자산운용 이벤트 | ✅ 성공 가능 |
| YouTube RSS | ✅ 성공 (API 없어도 가능) |
| Instagram | ❌ 봇 탐지 (구조적 한계) |
| 네이버 블로그 | ⚠️ iframe 구조 |
| 삼성증권 홈페이지 | ⚠️ SPA 구조 |
| 카카오톡 채널 | ❌ 구독자 전용 |
| KRX 보도자료 | ⚠️ 접속 차단 |
| KRX 거래실적 | ❌ SPA → 엑셀 업로드로 대체 |
| 구글 트렌드 | ✅ 성공 가능 |
| 퇴직연금 PDF | ❌ SPA + 로그인 |
| 뉴스 | ✅ 구글 RSS (네이버 API 없어도) |
