import feedparser
import httpx
import logging
from app.services.state_service import state_service
from app.services.discord_service import discord_service

logger = logging.getLogger(__name__)


class YouTubeService:
    async def check_updates(self, youtube_channel_id: str, discord_channel_id: str):
        """
        RSSを取得し、新着動画があればDiscordに通知する
        """
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={youtube_channel_id}"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(rss_url, timeout=10.0)
                response.raise_for_status()
                feed = feedparser.parse(response.text)
            except Exception as e:
                logger.error(f"RSS取得失敗: {e}")
                return

        if not feed.entries:
            return

        # 最新の動画エントリーを取得
        latest_entry = feed.entries[0]
        latest_video_id = latest_entry.yt_videoid
        video_title = latest_entry.title
        video_url = latest_entry.link

        # Firestoreから前回のIDを取得
        last_id = await state_service.get_last_video_id(youtube_channel_id)

        if latest_video_id != last_id:
            # 新着（または初回実行）の場合のみ通知
            message = f"🔴 **YouTube新着通知**\n**{video_title}**\n{video_url}"
            success = await discord_service.send_message(discord_channel_id, message)

            if success:
                # 通知に成功したらFirestoreを更新
                await state_service.update_last_video_id(youtube_channel_id, latest_video_id)
        else:
            logger.info(f"新着動画なし: {youtube_channel_id}")


youtube_service = YouTubeService()
