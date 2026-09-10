# Elyes 모집공고 알림 서비스 (2인 전용) 🚀

엘리스(Elyes) 임대주택 모집공고를 자동으로 크롤링하여 본인과 등록된 친구 1인에게 실시간 카카오톡 알림을 제공하는 시스템입니다.

## 주요 기능

- **자동 크롤링** — 매일 4회(9시, 12시, 15시, 18시) 새 모집공고 감지.
- **2인 맞춤 푸시** — 본인(나에게 보내기)과 친구(개별 토큰 기반)에게 각각 알림 전송.
- **세련된 초대장** — 친구에게 바이럴 느낌의 깔끔한 웹 초대장 제공.
- **프라이버시 보호** — '친구 목록' 권한 없이 각자의 권한으로 조심스럽게 작동.
- **마감 리마인더** — 공고 제목의 접수기간을 파싱해 접수 시작일 / 마감 전날 / 마감 당일에 재알림.
- **실시간 대시보드** — GitHub Pages를 통한 공고 이력, 진행 중인 모집(D-day), 분석 데이터 확인.

## 대시보드 및 초대장

- **실시간 대시보드**: [https://hee882.github.io/elyes-notify/](https://hee882.github.io/elyes-notify/)
- **친구 초대장**: `https://hee882.github.io/elyes-notify/subscribe.html?client_id=본인의_REST_API_KEY`

## 아키텍처

```
GitHub Actions (Cron)
  ├─ main.py          크롤링 + 듀얼 알림 엔진 (본인 + 친구)
  │   ├─ crawler.py     엘리스 웹사이트 스크래핑
  │   ├─ kakao_auth.py  OAuth 토큰 관리 (나/친구 각각 관리)
  │   ├─ notifier.py    카카오톡 메시지 전송 (Talk Memo API)
  │   └─ schedule.py    접수기간 파싱 + 마감 D-day 리마인더
  │
  ├─ analyzer.py      데이터 분석 및 경쟁률 계산
  │
  ├─ add_subscriber.py 친구용 토큰 추출 도구 (CLI)
  │
  ├─ tests/           pytest 테스트 스위트
  │
  └─ docs/            GitHub Pages 대시보드 & 랜딩 페이지
      ├─ index.html     알림 이력 및 분석 대시보드
      ├─ schedule.json  진행 중인 모집 접수기간
      └─ subscribe.html 세련된 친구 전용 구독 안내 페이지 🎁
```

## 마감 리마인더

공고 제목에는 접수기간이 다양한 형태로 들어있다.

```
[사송 롯데캐슬] 재임대 모집공고(접수기간 : 26.09.09~26.09.13)
[하단 롯데캐슬]재임대 모집공고(모집기간:26.09.08~26.09.10)
[어바니엘 천호] 공실세대 모집공고 (접수기간: 9/4~9/6)
[독산역 롯데캐슬] 59타입 재임대 모집공고(접수일시: '26. 9.11(금) 10시~15시)
```

`schedule.py`가 이를 파싱해 `docs/schedule.json`에 누적하고, 아래 시점에 카카오톡으로 재알림한다.

| 시점 | 메시지 |
|---|---|
| 접수 시작일 (공고일보다 늦게 시작할 때) | `[접수 시작] 오늘 접수 시작` |
| 마감 전날 | `[마감 D-1] 내일 접수 마감` |
| 마감 당일 | `[마감 D-DAY] 오늘 접수 마감` |

- 하루 4회 실행돼도 같은 리마인더는 한 번만 전송된다 (`reminded` 기록 기준).
- 전송에 실패하면 기록을 남기지 않아 다음 실행에서 재시도한다.
- 제목에 `마감되었습니다`가 붙은 공고는 대상에서 제외된다.
- 접수기간이 주말에 걸리는 공고가 있어 크론은 주말에도 실행된다.

전체 게시글에서 접수기간을 다시 추출하려면:

```bash
python schedule.py
```

## 테스트

```bash
pip install -r requirements-dev.txt
pytest
```

크롤링 HTML 파싱, 경쟁률 테이블 파싱, 접수기간 파싱, 당첨 확률/EWMA 예측,
알림 파이프라인(카카오 API는 모킹)을 검증한다. push/PR 시 GitHub Actions에서도 실행된다.

## 설치 및 설정

### 1. 초기 설정 (본인용)
1. [카카오 개발자 콘솔](https://developers.kakao.com)에서 앱 생성 후 **REST API 키** 복사.
2. **[카카오 로그인]** 활성화 및 Redirect URI(`http://localhost:3000`) 등록.
3. **[동의항목]**에서 `카카오톡 메시지 전송` 권한 설정.
4. 로컬에서 `python setup_kakao.py` 실행하여 본인 토큰 발급.

### 2. 친구 추가 방법 (개별 토큰 방식)
친구는 개발자 설정을 몰라도 됩니다. 아래 과정만 거치면 됩니다.
1. `python add_subscriber.py`를 실행해 생성된 초대장 링크를 친구에게 전달.
2. 친구가 링크에서 **[구독 시작하기]** 버튼 클릭 후 승인.
3. 친구가 알려준 **인증 코드**를 `add_subscriber.py`에 입력하여 **Refresh Token** 확보.
4. 확보된 토큰을 GitHub Secrets의 `KAKAO_FRIEND_REFRESH_TOKEN`에 등록.

### 3. GitHub Secrets 구성

| Name | Description |
|---|---|
| `KAKAO_REST_API_KEY` | 카카오 REST API 키 |
| `KAKAO_CLIENT_SECRET` | (선택) 카카오 보안 키 (사용 시 필수) |
| `KAKAO_REFRESH_TOKEN` | 본인 리프레시 토큰 |
| `KAKAO_FRIEND_REFRESH_TOKEN` | 친구 리프레시 토큰 |
| `GH_PAT` | 토큰 갱신을 위한 GitHub Personal Access Token |

## 보안 안내
- 이 프로젝트는 **공개 식별자(REST API KEY)** 외에 어떠한 민감한 토큰도 Git에 저장하지 않습니다.
- 모든 비밀번호와 토큰은 **GitHub Secrets**를 통해 안전하게 관리됩니다.
- 친구는 언제든 본인의 카카오톡 설정에서 앱 연결을 해지하여 알림을 중단할 수 있습니다.
