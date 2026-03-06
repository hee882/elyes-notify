"""
카카오 최초 토큰 발급 스크립트 (1회만 실행)

사용법:
  1. https://developers.kakao.com 에서 앱 생성
  2. [내 애플리케이션] > [앱 설정] > [앱 키] 에서 REST API 키 복사
  3. [내 애플리케이션] > [카카오 로그인] > 활성화
  4. [내 애플리케이션] > [카카오 로그인] > [Redirect URI] 에 http://localhost:3000 추가
  5. [내 애플리케이션] > [동의항목] > [카카오톡 메시지] > talk_message 활성화
  6. 이 스크립트 실행: python setup_kakao.py
"""
import webbrowser
from kakao_auth import get_initial_tokens


def main():
    print("=" * 50)
    print("  카카오톡 최초 토큰 발급")
    print("=" * 50)

    rest_api_key = input("\nREST API 키를 입력하세요: ").strip()
    client_secret = input("Client Secret (보안 키)를 입력하세요 (없으면 Enter): ").strip() or None
    redirect_uri = "http://localhost:3000"

    # 브라우저에서 카카오 로그인
    auth_url = (
        f"https://kauth.kakao.com/oauth/authorize"
        f"?client_id={rest_api_key}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
    )

    print(f"\n브라우저에서 카카오 로그인 페이지를 엽니다...")
    webbrowser.open(auth_url)

    print(f"\n로그인 후 리다이렉트된 URL에서 'code=' 뒤의 값을 복사하세요.")
    print(f"예: http://localhost:3000?code=XXXXXX 에서 XXXXXX 부분")
    auth_code = input("\n인증 코드를 입력하세요: ").strip()

    # 토큰 발급
    tokens = get_initial_tokens(rest_api_key, redirect_uri, auth_code, client_secret)

    print(f"\n{'=' * 50}")
    print(f"  토큰 발급 성공!")
    print(f"{'=' * 50}")
    print(f"\n아래 값을 GitHub Secrets에 등록하세요:\n")
    print(f"  KAKAO_REST_API_KEY    = {rest_api_key}")
    if client_secret:
        print(f"  KAKAO_CLIENT_SECRET   = {client_secret}")
    print(f"  KAKAO_REFRESH_TOKEN   = {tokens['refresh_token']}")
    print(f"\n(access_token은 자동 갱신되므로 저장 불필요)")


if __name__ == "__main__":
    main()
