from app.commands.handler import command_handler
from app.services.twitch_service import twitch_service
import logging

logger = logging.getLogger(__name__)


@command_handler.register("twitch")
async def handle_twitch(interaction: dict) -> dict:
    """
    /twitch コマンドのルーティング
    サブコマンド (register, list, delete) を解析して処理を分岐する
    """
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
        logger.error(f"Twitchコマンド実行エラー: {e}")
        return _error_response("Twitch APIとの通信中にエラーが発生しました。ログを確認してください。")


async def _handle_register(subcommand: dict) -> dict:
    # 引数からusernameを抽出
    args = subcommand.get("options", [])
    username = next((arg["value"] for arg in args if arg["name"] == "username"), None)

    if not username:
        return _error_response("ユーザー名が指定されていません。")

    user_id = await twitch_service.get_user_id(username)
    if not user_id:
        return _error_response(f"Twitchユーザー '{username}' が見つかりませんでした。")

    success = await twitch_service.create_eventsub_subscription(user_id)
    if success:
        return _success_response(f"✅ '{username}' (ID: {user_id}) の配信通知を登録しました。")
    else:
        return _error_response(f"❌ '{username}' の登録に失敗しました。")


async def _handle_list() -> dict:
    subs = await twitch_service.get_subscriptions()
    if not subs:
        return _success_response("現在登録されているTwitchの配信通知はありません。")

    # 1. 登録されている全EventSubからユーザーIDを抽出
    user_ids = [
        sub.get("condition", {}).get("broadcaster_user_id")
        for sub in subs
        if sub.get("condition", {}).get("broadcaster_user_id")
    ]

    # 2. 抽出したIDを元に、ユーザー名のマッピング辞書を一括取得 (API通信はここでの1回のみ)
    user_map = await twitch_service.get_users_by_ids(user_ids)

    # 3. 辞書を使ってリストを整形
    lines = ["**【登録済みの配信通知一覧】**"]
    for sub in subs:
        sub_id = sub.get("id")
        user_id = sub.get("condition", {}).get("broadcaster_user_id", "Unknown")
        status = sub.get("status")

        # 辞書からユーザー名を取得（万が一取れなかった場合は "UnknownUser" とフォールバック）
        username = user_map.get(user_id, "UnknownUser")

        lines.append(f"- **{username}** (ID: `{user_id}`) | Status: `{status}`\n  └ SubID: `{sub_id}`")

    return _success_response("\n".join(lines))


async def _handle_delete(subcommand: dict) -> dict:
    args = subcommand.get("options", [])
    sub_id = next((arg["value"] for arg in args if arg["name"] == "subscription_id"), None)

    if not sub_id:
        return _error_response("削除するSubscription IDが指定されていません。")

    success = await twitch_service.delete_subscription(sub_id)
    if success:
        return _success_response(f"🗑️ Subscription ID `{sub_id}` を削除しました。")
    else:
        return _error_response("❌ 削除に失敗しました。IDが間違っている可能性があります。")


def _success_response(message: str) -> dict:
    return {"type": 4, "data": {"content": message}}


def _error_response(message: str) -> dict:
    return {"type": 4, "data": {"content": f"⚠️ {message}"}}
