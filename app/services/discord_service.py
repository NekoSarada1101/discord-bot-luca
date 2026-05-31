import logging
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class DiscordService:
    def __init__(self):
        self.base_url = "https://discord.com/api/v10"
        self.headers = {
            "Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json"
        }

    async def send_message(self, channel_id: str, content: str) -> bool:
        """
        指定されたチャンネルIDにテキストメッセージを送信する。
        """
        url = f"{self.base_url}/channels/{channel_id}/messages"
        payload = {"content": content}

        # httpxの非同期クライアントを使用してリクエストを送信
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, headers=self.headers)

                if response.status_code == 200:
                    logger.info(f"Discordへのメッセージ送信に成功しました。Channel: {channel_id}")
                    return True
                else:
                    logger.error(f"Discord API エラー: {response.status_code} - {response.text}")
                    return False

            except Exception as e:
                logger.error(f"Discordへのリクエスト中に例外が発生しました: {e}")
                return False

    async def edit_original_response(self, application_id: str, token: str, content: str) -> bool:
        """
        Interaction APIで「考え中(Type 5)」として返したメッセージを後から更新する
        """
        # Webhook用の特殊なエンドポイント (Bot TokenのAuthorizationヘッダは不要)
        url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
        payload = {"content": content}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(url, json=payload)
                if response.status_code == 200:
                    return True
                else:
                    logger.error(f"Interaction上書きエラー: {response.status_code} - {response.text}")
                    return False
            except Exception as e:
                logger.error(f"Discord WebhookへのPATCH中に例外発生: {e}")
                return False


# シングルトンパターンとしてインスタンスをエクスポート
discord_service = DiscordService()
