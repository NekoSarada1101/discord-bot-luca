import logging
from app.commands.handler import command_handler
from app.services.finops_service import finops_service

logger = logging.getLogger(__name__)


@command_handler.register("finops")
async def handle_finops(interaction: dict) -> dict:
    options = interaction.get("data", {}).get("options", [])
    if not options:
        return _error_response("サブコマンドが指定されていません。")

    subcommand = options[0]
    sub_name = subcommand.get("name")

    try:
        if sub_name == "audit":
            return await _handle_audit(subcommand)
        else:
            return _error_response(f"不明なサブコマンド: {sub_name}")
    except Exception as e:
        logger.error(f"FinOpsコマンド実行エラー: {e}")
        return _error_response("処理中に内部エラーが発生しました。")


async def _handle_audit(subcommand: dict) -> dict:
    args = subcommand.get("options", [])
    days = 7  # デフォルト
    for arg in args:
        if arg["name"] == "days":
            days = int(arg["value"])
            break

    if days <= 0 or days > 30:
        return _error_response("日数は1日から30日の間で指定してください。")

    try:
        # 監査の実行とレポート生成
        report = await finops_service.perform_weekly_audit(days=days)
        return {"type": 4, "data": {"content": report}}
    except Exception as e:
        return _error_response(f"FinOps監査の実行中にエラーが発生しました: {e}")


def _error_response(message: str) -> dict:
    return {"type": 4, "data": {"content": f"⚠️ {message}"}}
