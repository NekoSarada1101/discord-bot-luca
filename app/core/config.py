from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # アプリケーションのベース設定
    PORT: int = 8080
    APP_PUBLIC_URL: str

    # Discord 関連
    DISCORD_APP_ID: str
    DISCORD_PUBLIC_KEY: str
    DISCORD_BOT_TOKEN: str
    DISCORD_STREAMING_CHANNEL_ID: str

    # Twitch 関連
    TWITCH_CLIENT_ID: str
    TWITCH_CLIENT_SECRET: str
    TWITCH_WEBHOOK_SECRET: str

    # YouTube (Cron) 関連
    CRON_SECRET: str

    # FinOps (GCS / BigQuery) 関連
    PROJECT_ID: str
    FINOPS_BQ_DATASET: str
    ENEOS_BQ_TABLE: str
    PC_POWER_BQ_TABLE: str
    DISCORD_FINOPS_CHANNEL_ID: str
    GEMINI_API_KEY: str
    ELECTRICITY_UNIT_PRICE: float = 35.0

    # Pydantic Settingsの設定（.envファイルからの読み込みを有効化）
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # 定義されていない環境変数が存在しても無視する
    )


settings = Settings()
