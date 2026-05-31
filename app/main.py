from fastapi import FastAPI
import logging

from app.api.routers import discord, twitch, youtube
from app.core.config import settings

# 仮のロガー設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# ---------------------------------------------------------
# ルーターの登録 (各プラットフォームからのWebhook受け口)
# ※各ファイルが実装されたらコメントアウトを外す
# ---------------------------------------------------------
app.include_router(discord.router, prefix="/api/discord", tags=["Discord"])
app.include_router(twitch.router, prefix="/api/twitch", tags=["Twitch"])
app.include_router(youtube.router, prefix="/api/youtube", tags=["YouTube"])


@app.get("/", tags=["System"])
async def health_check():
    """
    Cloud Runのヘルスチェック用エンドポイント
    """
    return {"status": "ok", "message": "Bot API is running stably."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.PORT, reload=True)
