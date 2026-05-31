from app.commands.handler import command_handler


@command_handler.register("status")
async def status_command(interaction: dict) -> dict:
    """
    /status コマンドの処理
    """
    # 呼び出したユーザーの情報を取得 (サーバー内かDMかで構造が変わるためフォールバックを入れる)
    user = interaction.get("member", {}).get("user") or interaction.get("user", {})
    username = user.get("username", "ユーザー")

    # Discord Interaction APIが求める応答フォーマットを返す
    return {
        "type": 4,  # Type 4: ChannelMessageWithSource (即時応答)
        "data": {
            "content": f"🟢 システムは正常に稼働しています、{username}さん。\n稼働環境: Google Cloud Run"
        }
    }


# --- 今後新しいコマンドを追加する場合は、以下のように関数を増やすか、別ファイルを作成する ---
@command_handler.register("ping")
async def ping_command(interaction: dict) -> dict:
    return {
        "type": 4,
        "data": {"content": "🏓 Pong!"}
    }
