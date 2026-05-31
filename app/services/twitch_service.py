import logging
import httpx
from typing import Optional, List, Dict, Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class TwitchService:
    def __init__(self):
        self.oauth_url = "https://id.twitch.tv/oauth2/token"
        self.api_url = "https://api.twitch.tv/helix"
        self._app_access_token: Optional[str] = None

    async def _get_access_token(self) -> str:
        """
        App Access Tokenを取得（または再利用）する。
        ※本来は有効期限(expires_in)を管理すべきだが、今回はリクエストごとの取得失敗時に再取得するシンプルなキャッシュ構造とする。
        """
        if self._app_access_token:
            return self._app_access_token

        payload = {
            "client_id": settings.TWITCH_CLIENT_ID,
            "client_secret": settings.TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials"
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.oauth_url, data=payload)
            if response.status_code == 200:
                data = response.json()
                self._app_access_token = data.get("access_token")
                logger.info("Twitch App Access Token の取得に成功した。")
                return self._app_access_token
            else:
                logger.error(f"Token取得失敗: {response.text}")
                raise Exception("Twitch App Access Tokenの取得に失敗した。")

    async def _make_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """
        全API共通のリクエストヘルパー。
        401検知時にキャッシュをクリアして最大1回リトライする。
        """
        url = f"{self.api_url}/{path.lstrip('/')}"
        for attempt in range(2):
            token = await self._get_access_token()  # ここでキャッシュまたは新規取得
            headers = {
                "Client-Id": settings.TWITCH_CLIENT_ID,
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            async with httpx.AsyncClient() as client:
                response = await client.request(method, url, headers=headers, **kwargs)

                if response.status_code == 401 and attempt == 0:
                    logger.warning("Twitchトークン失効を検知。キャッシュをクリアしてリトライします。")
                    self._app_access_token = None  # キャッシュ破棄
                    continue  # ループの最初に戻り、次の _get_access_token で新規発行される

                return response

        raise Exception(f"Twitch API通信エラー (Retry failed): {response.status_code} - {response.text}")

    async def get_user_id(self, username: str) -> Optional[str]:
        """
        Twitchの表示名（login）から一意のユーザーIDを取得する。
        EventSubの登録にはこのIDが必須となる。
        """
        response = await self._make_request("GET", f"users?login={username.lower()}")

        if response.status_code == 200:
            data = response.json().get("data", [])
            return data[0].get("id") if data else None

        logger.warning(f"ユーザーIDの取得に失敗、または存在しないユーザー: {username}")
        return None

    async def get_users_by_ids(self, user_ids: list) -> dict:
        """
        複数のユーザーIDからユーザー名(login)の辞書を一括生成する。
        N+1問題を回避するためのバルク処理。
        """
        if not user_ids:
            return {}

        # 重複を排除し、TwitchAPIの制限である最大100件でクリップする
        unique_ids = list(set(user_ids))[:100]

        # id=111&id=222&id=333 のクエリストリングを生成
        query_string = "&".join([f"id={uid}" for uid in unique_ids])
        response = await self._make_request("GET", f"/users?{query_string}")

        if response.status_code == 200:
            data = response.json().get("data", [])
            # { "12345": "testBroadcaster", ... } の辞書を返す
            return {user["id"]: user["login"] for user in data}

        logger.error(f"バルクユーザー取得失敗: {response.text}")
        return {}

    async def create_eventsub_subscription(self, user_id: str) -> bool:
        """
        特定のユーザーIDに対する stream.online イベントのWebhook購読を登録する。
        """
        payload = {
            "type": "stream.online",
            "version": "1",
            "condition": {
                "broadcaster_user_id": user_id
            },
            "transport": {
                "method": "webhook",
                "callback": f"{settings.APP_PUBLIC_URL}/api/twitch/eventsub",
                "secret": settings.TWITCH_WEBHOOK_SECRET
            }
        }

        response = await self._make_request("POST", "/eventsub/subscriptions", json=payload)

        if response.status_code == 202:
            logger.info(f"EventSub登録リクエスト受理 (UserID: {user_id})")
            return True
        elif response.status_code == 409:
            logger.info(f"既に登録されているEventSub (UserID: {user_id})")
            return True
        else:
            logger.error(f"EventSub登録失敗: {response.status_code} - {response.text}")
            return False

    async def get_subscriptions(self) -> List[Dict[str, Any]]:
        """
        現在登録されているすべてのEventSub購読一覧を取得する。
        """
        response = await self._make_request("GET", "/eventsub/subscriptions")

        if response.status_code == 200:
            return response.json().get("data", [])
        return []

    async def delete_subscription(self, subscription_id: str) -> bool:
        """
        指定したIDのEventSub購読を削除する。
        """
        response = await self._make_request("DELETE", f"/eventsub/subscriptions?id={subscription_id}")

        if response.status_code == 204:
            logger.info(f"EventSub購読を削除した (ID: {subscription_id})")
            return True
        else:
            logger.error(f"EventSub削除失敗: {response.status_code} - {response.text}")
            return False


twitch_service = TwitchService()
