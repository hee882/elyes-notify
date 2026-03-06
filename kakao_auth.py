import os
import requests

TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def refresh_access_token(rest_api_key=None, refresh_token=None, client_secret=None):
    """refresh_token으로 새 access_token을 발급받는다.
    refresh_token 잔여기간이 1개월 미만이면 새 refresh_token도 함께 반환된다.

    Returns:
        dict: {"access_token": str, "new_refresh_token": str or None}
    """
    api_key = rest_api_key or os.getenv("KAKAO_REST_API_KEY")
    rt = refresh_token or os.getenv("KAKAO_REFRESH_TOKEN")
    secret = client_secret or os.getenv("KAKAO_CLIENT_SECRET")

    data = {
        "grant_type": "refresh_token",
        "client_id": api_key,
        "refresh_token": rt,
    }
    if secret:
        data["client_secret"] = secret

    resp = requests.post(TOKEN_URL, data=data, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"카카오 토큰 갱신 실패: {data['error_description']}")

    return {
        "access_token": data["access_token"],
        "new_refresh_token": data.get("refresh_token"),  # 갱신된 경우에만 존재
    }


def get_initial_tokens(rest_api_key, redirect_uri, auth_code, client_secret=None):
    """최초 인증코드로 access_token + refresh_token을 발급받는다."""
    data = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": redirect_uri,
        "code": auth_code,
    }
    if client_secret:
        data["client_secret"] = client_secret

    resp = requests.post(TOKEN_URL, data=data, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"토큰 발급 실패: {data['error_description']}")

    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
    }
