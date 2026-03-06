# Elyes 모집공고 알림

엘리스(Elyes) 임대주택 모집공고를 자동으로 크롤링하여 카카오톡으로 알림을 보내고, 경쟁률을 분석하는 시스템.

## 주요 기능

- **자동 크롤링** — 평일 4회(9시, 12시, 15시, 18시) 새 모집공고 감지
- **카카오톡 알림** — 새 공고 발견 시 즉시 카카오톡 메시지 전송 (이미지 포함)
- **실패 재전송** — 전송 실패 건 자동 재시도
- **경쟁률 분석** — 단지별/타입별 경쟁률 추적 및 당첨 확률 산출
- **수요 분산 모델** — 동시 모집 타입 수에 따른 수요 분산 효과 반영
- **대시보드** — GitHub Pages 기반 실시간 대시보드

## 대시보드

**[https://hee882.github.io/elyes-notify/](https://hee882.github.io/elyes-notify/)**

| 탭 | 내용 |
|---|------|
| 최근 알림 이력 | 알림 발송 기록 (상태, 제목, 날짜) + 페이징 |
| 경쟁률 분석 | 단지별 타입 비교, 수요 분산 컨텍스트, 당첨 확률 |
| 당첨 계산기 | 백테스트 모델 검증 (MAPE, 적중률, CI 커버리지) |

## 아키텍처

```
GitHub Actions (cron)
  ├─ main.py          크롤링 + 카카오톡 알림
  │   ├─ crawler.py     엘리스 API 스크래핑
  │   ├─ kakao_auth.py  OAuth 토큰 관리
  │   └─ notifier.py    카카오톡 메시지 전송
  │
  ├─ analyzer.py      경쟁률 분석 + 확률 최적화
  │   ├─ 경쟁률 EWMA 예측 (수요 분산 반영)
  │   ├─ 당첨 확률 계산 (직접 + 예비번호)
  │   ├─ 몬테카를로 시뮬레이션
  │   └─ Walk-forward 백테스트
  │
  └─ docs/            GitHub Pages 대시보드
      ├─ index.html     단일 페이지 대시보드
      ├─ history.json   알림 이력
      ├─ analysis.json  분석 결과
      └─ archive.json   누적 경쟁률 데이터
```

## 설치 및 설정

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 카카오 API 설정

1. [카카오 개발자 콘솔](https://developers.kakao.com/console/app)에서 앱 생성
2. REST API 키 복사
3. 카카오 로그인 → 동의항목에서 `talk_message` 활성화
4. 초기 토큰 발급:

```bash
python setup_kakao.py
```

### 3. GitHub Secrets 등록

| Secret | 설명 |
|--------|------|
| `KAKAO_REST_API_KEY` | REST API 키 |
| `KAKAO_CLIENT_SECRET` | 클라이언트 시크릿 |
| `KAKAO_REFRESH_TOKEN` | 리프레시 토큰 (자동 갱신) |
| `GH_PAT` | GitHub Personal Access Token (시크릿 업데이트용) |

### 4. GitHub Pages 활성화

Settings → Pages → Source: `Deploy from a branch` → `main` / `docs`

## 분석 모델

- **EWMA 예측** — 지수가중이동평균으로 경쟁률 예측 (alpha 자동 튜닝)
- **수요 분산** — 단독 모집 vs 복수 타입 동시 모집 시 진입 수요 차이 반영
- **예비번호** — 3배수 예비번호, 30% 전환율 적용
- **백테스트** — Walk-forward 검증 (MAPE ~40%, 타입 선택 정확도 90%)

## 데이터 흐름

```
엘리스 웹사이트 API
  → crawler.py (스크래핑)
  → main.py (신규 감지 + 카카오톡 전송)
  → docs/history.json (이력 저장)

  → analyzer.py (전체 크롤링 + 매칭)
  → docs/archive.json (누적 데이터)
  → docs/analysis.json (분석 결과)

  → git commit + push
  → GitHub Pages 자동 배포
  → docs/index.html (대시보드 렌더링)
```
