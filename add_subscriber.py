import os
from dotenv import load_dotenv
from kakao_auth import get_initial_tokens

def main():
    load_dotenv()
    
    rest_api_key = os.getenv("KAKAO_REST_API_KEY")
    if not rest_api_key:
        print("에러: KAKAO_REST_API_KEY가 설정되지 않았습니다.")
        return

    # 브라우저(subscribe.html)에서 사용한 것과 동일한 redirect_uri를 사용해야 401 에러가 발생하지 않습니다.
    redirect_uri = f"https://hee882.github.io/elyes-notify/subscribe.html"
    
    print("=" * 60)
    print("  카카오톡 공지 알림 - 친구용 토큰 추출 도구")
    print("=" * 60)
    print("\n1. 친구에게 아래 [초대 링크]를 복사해서 보내주세요.")
    print("   (카톡에 붙여넣으면 예쁜 미리보기 카드가 뜹니다! 🚀)")
    
    # GitHub Pages 주소 안내
    subscribe_url = f"https://hee882.github.io/elyes-notify/subscribe.html?client_id={rest_api_key}"
    
    print(f"\n👉 초대 링크: {subscribe_url}")
    
    print("\n2. 친구가 링크에서 [구독 시작하기]를 누르고 로그인을 마치면")
    print("   화면에 나타나는 [인증 코드]를 받아서 여기에 입력하세요.")
    auth_code = input("\n인증 코드 입력: ").strip()
    
    if not auth_code:
        print("취소되었습니다.")
        return
        
    try:
        # 친구의 일회성 코드로 친구의 리프레시 토큰 발급
        client_secret = os.getenv("KAKAO_CLIENT_SECRET")
        tokens = get_initial_tokens(rest_api_key, redirect_uri, auth_code, client_secret)
        
        refresh_token = tokens["refresh_token"]
        
        print(f"\n{'=' * 60}")
        print("  친구의 리프레시 토큰 추출 성공!")
        print(f"{'=' * 60}")
        print(f"\n복사할 값:\n\n{refresh_token}")
        print(f"\n{'=' * 60}")
        print("\n위 값을 복사하여 GitHub Secrets의 [KAKAO_FRIEND_REFRESH_TOKEN] 항목에 등록하세요.")
        print("이제 나와 이 친구, 총 2명에게 공지가 각각 자동으로 발송됩니다!")
            
    except Exception as e:
        print(f"\n토큰 발급 실패: {e}")
        print("코드가 만료되었거나 이미 사용되었을 수 있습니다. 친구에게 다시 시도를 요청하세요.")

if __name__ == "__main__":
    main()
