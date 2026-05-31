import csv
import io
import logging
import zoneinfo
from datetime import datetime, timezone

from google.cloud import bigquery, storage

from app.core.config import settings
from app.services.discord_service import discord_service

logger = logging.getLogger(__name__)


class FinOpsService:
    def __init__(self):
        self.jst = zoneinfo.ZoneInfo("Asia/Tokyo")
        self.full_table_id = (
            f"{settings.PROJECT_ID}.{settings.FINOPS_BQ_DATASET}.{settings.FINOPS_BQ_TABLE}"
        )

    async def process_eneos_csv_and_notify(self, bucket_name: str, file_name: str):
        """
        GCSからCSVを取得し、BigQueryへロードした結果をDiscordへ通知する。
        """
        try:
            logger.info(f"FinOps ETL 開始: gs://{bucket_name}/{file_name}")

            storage_client = storage.Client(project=settings.PROJECT_ID)
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(file_name)
            csv_data = blob.download_as_text(encoding="utf-8-sig")

            rows_to_insert = []
            reader = csv.DictReader(io.StringIO(csv_data))

            for row in reader:
                try:
                    date_str = row["対象日"]
                    time_str = row["開始時間"]
                    dt_jst = datetime.strptime(
                        f"{date_str} {time_str}", "%Y%m%d %H:%M"
                    ).replace(tzinfo=self.jst)
                    timestamp_utc = dt_jst.astimezone(timezone.utc).isoformat()

                    usage_str = row["使用量"].strip()
                    usage_kwh = (
                        None if (usage_str == "-" or not usage_str) else float(usage_str)
                    )

                    rows_to_insert.append({
                        "timestamp": timestamp_utc,
                        "customer_number": row["お客さま番号"],
                        "usage_kwh": usage_kwh,
                    })
                except Exception as row_error:
                    logger.warning(
                        f"不正な行をスキップ: {row_error}. Data: {row}"
                    )
                    continue

            if rows_to_insert:
                bq_client = bigquery.Client(project=settings.PROJECT_ID)
                errors = bq_client.insert_rows_json(self.full_table_id, rows_to_insert)

                if errors:
                    raise RuntimeError(f"BigQuery write failure: {errors}")

                success_msg = (
                    f"✅ **FinOps ETL Pipeline Success**\n"
                    f"ENEOSの30分別電力データの取り込みが完了しました。\n"
                    f"- ソース: `gs://{bucket_name}/{file_name}`\n"
                    f"- ロード件数: `{len(rows_to_insert)}` 件"
                )
                logger.info(success_msg)
                await discord_service.send_message(
                    settings.DISCORD_FINOPS_CHANNEL_ID, success_msg
                )
            else:
                await discord_service.send_message(
                    settings.DISCORD_FINOPS_CHANNEL_ID,
                    f"⚠️ **FinOps ETL Pipeline Warning**\n"
                    f"`{file_name}` から有効なレコードを抽出できませんでした。",
                )

        except Exception as e:
            error_msg = (
                f"❌ **FinOps ETL Pipeline Critical Error**\n"
                f"ファイル `{file_name}` の処理中に致命的な例外が発生しました。\n"
                f"```\n{e}\n```"
            )
            logger.error(error_msg)
            await discord_service.send_message(
                settings.DISCORD_FINOPS_CHANNEL_ID, error_msg
            )


finops_service = FinOpsService()
