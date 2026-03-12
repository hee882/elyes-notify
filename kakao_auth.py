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
    if resp.status_code != 200:
        raise RuntimeError(f"카카오 토큰 갱신 실패 ({resp.status_code}): {resp.text}")

    result = resp.json()
    if "error" in result:
        raise RuntimeError(f"카카오 토큰 갱신 실패: {result['error_description']}")

    return {
        "access_token": result["access_token"],
        "new_refresh_token": result.get("refresh_token"),
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
    
    if resp.status_code != 200:
        error_msg = f"토큰 발급 실패 ({resp.status_code})"
        try:
            err_data = resp.json()
            if "error_description" in err_data:
                error_msg += f": {err_data['error_description']} ({err_data.get('error')})"
            else:
                error_msg += f": {resp.text}"
        except:
            error_msg += f": {resp.text}"
        raise RuntimeError(error_msg)

    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
    }
