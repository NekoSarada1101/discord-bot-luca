from fastapi import APIRouter, HTTPException, Header
from app.core.config import settings
from app.services.youtube_service import youtube_service
from app.services.state_service import state_service

router = APIRouter()


@router.get("/cron")
async def youtube_cron(authorization: str = Header(None)):
    if authorization != f"Bearer {settings.CRON_SECRET}":
        raise HTTPException(status_code=403, detail="Unauthorized cron request")

    # Firestoreから監視対象の全チャンネルを取得
    target_channels = await state_service.get_all_youtube_channels()

    for ch in target_channels:
        await youtube_service.check_updates(ch["id"], settings.DISCORD_STREAMING_CHANNEL_ID)

    return {"status": "completed", "processed_channels": len(target_channels)}
