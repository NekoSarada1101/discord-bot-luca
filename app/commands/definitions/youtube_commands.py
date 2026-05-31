import httpx
from app.commands.handler import command_handler
from app.services.state_service import state_service
import feedparser
import logging

logger = logging.getLogger(__name__)


@command_handler.register("youtube")
async def handle_youtube(interaction: dict) -> dict:
    options = interaction.get("data", {}).get("options", [])
    if not options:
        return _error_response("サブコマンドが指定されていません。")

    subcommand = options[0]
    sub_name = subcommand.get("name")

    try:
        if sub_name == "register":
            return await _handle_register(subcommand)
        elif sub_name == "list":
            return await _handle_list()
        elif sub_name == "delete":
            return await _handle_delete(subcommand)
        else:
            return _error_response(f"不明なサブコマンド: {sub_name}")
    except Exception as e:
        logger.error(f"YouTubeコマンド実行エラー: {e}")
        return _error_response("処理中に内部エラーが発生しました。")


async def _handle_register(subcommand: dict) -> dict:
    args = subcommand.get("options", [])
    channel_id = next((arg["value"] for arg in args if arg["name"] == "channel_id"), None)

    if not channel_id or not channel_id.startswith("UC"):
        return _error_response("無効なチャンネルIDです。'UC'から始まるIDを指定してください。")

    # RSSを取得してチャンネルの存在確認とタイトルの抽出を行う
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(rss_url)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
        except Exception:
            return _error_response(f"チャンネルID `{channel_id}` から情報を取得できませんでした。")

    if not feed.feed:
        return _error_response("無効なチャンネルです。")

    channel_title = feed.feed.title

    success = await state_service.add_youtube_channel(channel_id, channel_title)
    if success:
        return _success_response(f"✅ YouTubeチャンネル **{channel_title}** (ID: `{channel_id}`) を登録しました。")
    else:
        return _error_response(f"⚠️ **{channel_title}** は既に登録されています。")


async def _handle_list() -> dict:
    channels = await state_service.get_all_youtube_channels()
    if not channels:
        return _success_response("現在登録されているYouTubeの通知はありません。")

    lines = ["**【登録済みのYouTube通知一覧】**"]
    for ch in channels:
        ch_id = ch.get("id")
        title = ch.get("title", "Unknown Channel")
        lines.append(f"- **{title}**\n  └ ID: `{ch_id}`")

    return _success_response("\n".join(lines))


async def _handle_delete(subcommand: dict) -> dict:
    args = subcommand.get("options", [])
    channel_id = next((arg["value"] for arg in args if arg["name"] == "channel_id"), None)

    if not channel_id:
        return _error_response("チャンネルIDが指定されていません。")

    success = state_service.delete_youtube_channel(channel_id)
    if success:
        return _success_response(f"🗑️ YouTubeチャンネル ID `{channel_id}` の登録を解除しました。")
    else:
        return _error_response("❌ 削除に失敗しました。IDが未登録の可能性があります。")


def _success_response(message: str) -> dict:
    return {"type": 4, "data": {"content": message}}


def _error_response(message: str) -> dict:
    return {"type": 4, "data": {"content": f"⚠️ {message}"}}
