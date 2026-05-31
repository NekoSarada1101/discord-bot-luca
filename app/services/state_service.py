import logging
from google.cloud import firestore
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


class StateService:
    def __init__(self):
        self.db = firestore.AsyncClient()
        self.yt_collection = "luca_bot_youtube_state"

    async def add_youtube_channel(self, channel_id: str, title: str) -> bool:
        """YouTubeチャンネルを登録する"""
        doc_ref = self.db.collection(self.yt_collection).document(channel_id)
        doc = await doc_ref.get()
        if doc.exists:
            return False  # 既に登録済み
        await doc_ref.set({"title": title, "last_video_id": None})
        return True

    async def get_all_youtube_channels(self) -> List[Dict[str, str]]:
        """登録されているすべてのYouTubeチャンネルを取得する"""
        docs = self.db.collection(self.yt_collection).stream()
        return [{"id": doc.id, **doc.to_dict()} async for doc in docs]

    async def delete_youtube_channel(self, channel_id: str) -> bool:
        """指定したYouTubeチャンネルの登録を解除する"""
        doc_ref = self.db.collection(self.yt_collection).document(channel_id)
        doc = await doc_ref.get()
        if not doc.exists:
            return False
        await doc_ref.delete()
        return True

    async def get_last_video_id(self, channel_id: str) -> Optional[str]:
        """指定したYouTubeチャンネルの最後に通知済みの動画IDを取得"""
        doc_ref = self.db.collection(self.yt_collection).document(channel_id)
        doc = await doc_ref.get()
        if doc.exists:
            return doc.to_dict().get("last_video_id")
        return None

    async def update_last_video_id(self, channel_id: str, video_id: str):
        """通知済みの動画IDを更新"""
        doc_ref = self.db.collection(self.yt_collection).document(channel_id)
        await doc_ref.set({"last_video_id": video_id}, merge=True)
        logger.info(f"Firestoreを更新: {channel_id} -> {video_id}")


state_service = StateService()
