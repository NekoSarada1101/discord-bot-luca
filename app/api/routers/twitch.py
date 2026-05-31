from fastapi import APIRouter, HTTPException, Request, Depends, Response
from app.core.security import verify_twitch_signature
from app.core.config import settings
from app.services.discord_service import discord_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/eventsub", dependencies=[Depends(verify_twitch_signature)])
async def twitch_eventsub(request: Request):
    body = await request.json()

    # Twitch EventSubのリクエストタイプはヘッダーで識別する
    message_type = request.headers.get("Twitch-Eventsub-Message-Type")

    # 1. 登録時のコールバック検証処理
    if message_type == "webhook_callback_verification":
        challenge = body.get("challenge")
        # challengeの文字列をそのまま返す
        return Response(content=challenge, media_type="text/plain")

    # 2. 実際のイベント通知
    if message_type == "notification":
        event_data = body.get("event", {})
        broadcaster_name = event_data.get("broadcaster_user_name")
        event_type = body.get("subscription", {}).get("type")

        if event_type == "stream.online":
            twitch_url = f"https://twitch.tv/{event_data.get('broadcaster_user_login')}"
            message_content = f"💜 **{broadcaster_name}** がTwitchで配信を開始しました！\n{twitch_url}"

            success = await discord_service.send_message(
                channel_id=settings.DISCORD_STREAMING_CHANNEL_ID,
                content=message_content
            )

            if not success:
                logger.error(f"Discord通知失敗。Twitchへの応答を保留し、再送を要求します。 (User: {broadcaster_name})")
                raise HTTPException(status_code=500, detail="Failed to notify Discord. Please retry.")

        return {"status": "event_received"}

    return {"status": "ignored_message_type"}
