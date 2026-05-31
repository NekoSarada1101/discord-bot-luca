import logging
from typing import Callable, Dict

logger = logging.getLogger(__name__)


class CommandHandler:
    def __init__(self):
        # コマンド名と実行関数のマッピングを保持する辞書
        self._registry: Dict[str, Callable] = {}

    def register(self, name: str):
        """
        コマンドを登録するためのデコレータ
        使用例: @command_handler.register("status")
        """
        def decorator(func: Callable):
            if name in self._registry:
                logger.warning(f"コマンド '{name}' は既に登録されています。上書きします。")
            self._registry[name] = func
            logger.info(f"コマンド '{name}' を登録しました。")
            return func
        return decorator

    async def execute(self, interaction: dict) -> dict:
        """
        DiscordからのInteractionペイロードを受け取り、適切な関数を実行する
        """
        command_data = interaction.get("data", {})
        command_name = command_data.get("name")

        func = self._registry.get(command_name)
        if not func:
            logger.error(f"未登録のコマンドが呼び出されました: {command_name}")
            # コマンドが見つからない場合のエラー応答
            return {
                "type": 4,
                "data": {"content": f"⚠️ コマンド `{command_name}` の処理が実装されていません。"}
            }

        try:
            # 登録されたコマンド処理を実行し、その結果（辞書）を返す
            return await func(interaction)
        except Exception as e:
            logger.error(f"コマンド '{command_name}' の実行中にエラーが発生: {e}")
            return {
                "type": 4,
                "data": {"content": "❌ コマンドの実行中に内部エラーが発生しました。"}
            }

    async def execute_and_patch(self, interaction: dict):
        """
        バックグラウンドでコマンドを実行し、結果をDiscordにPATCH送信するラッパー
        """
        try:
            # 既存のロジックをそのまま実行し、 {"type": 4, "data": {"content": "..."}} を受け取る
            result = await self.execute(interaction)
            content = result.get("data", {}).get("content", "処理が完了しました")
        except Exception as e:
            logger.error(f"バックグラウンド処理エラー: {e}")
            content = "❌ 内部エラーが発生しました。ログを確認してください。"

        # Interactionのメタデータを取得
        app_id = interaction.get("application_id")
        token = interaction.get("token")

        # 結果をDiscordへ事後送信
        from app.services.discord_service import discord_service
        await discord_service.edit_original_response(app_id, token, content)


# アプリケーション全体で使い回すシングルトンインスタンス
command_handler = CommandHandler()


def _load_command_definitions() -> None:
    import importlib

    importlib.import_module("app.commands")


_load_command_definitions()
