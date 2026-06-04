import asyncio
import csv
import io
import logging
import zoneinfo
from datetime import datetime, timezone

from google.cloud import bigquery, storage
import google.generativeai as genai

from app.core.config import settings
from app.services.discord_service import discord_service

logger = logging.getLogger(__name__)


class FinOpsService:
    def __init__(self):
        self.jst = zoneinfo.ZoneInfo("Asia/Tokyo")
        self.full_table_id = (
            f"{settings.PROJECT_ID}.{settings.FINOPS_BQ_DATASET}.{settings.ENEOS_BQ_TABLE}"
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

    async def get_weekly_power_data(self, days: int = 3) -> dict:
        """
        BigQueryからPC消費電力(15分値)と家庭消費電力量(30分値)を取得・結合し、
        相関分析・ベースロード推定等の集計結果を返す。
        """
        pc_table_id = f"{settings.PROJECT_ID}.{settings.FINOPS_BQ_DATASET}.{settings.PC_POWER_BQ_TABLE}"
        household_table_id = f"{settings.PROJECT_ID}.{settings.FINOPS_BQ_DATASET}.{settings.ENEOS_BQ_TABLE}"

        query = f"""
        WITH pc_raw AS (
          SELECT
            TIMESTAMP_SECONDS(DIV(UNIX_SECONDS(timestamp), 1800) * 1800) AS time_30m,
            hostname,
            total_power_w
          FROM
            `{pc_table_id}`
          WHERE
            timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        ),
        pc_30m AS (
          SELECT
            time_30m,
            hostname,
            AVG(total_power_w) * 0.5 / 1000.0 AS pc_kwh
          FROM
            pc_raw
          GROUP BY
            time_30m, hostname
        ),
        pc_pivot AS (
          SELECT
            time_30m,
            SUM(pc_kwh) AS pc_total_kwh,
            SUM(CASE WHEN hostname = 'main-pc' THEN pc_kwh ELSE 0.0 END) AS main_pc_kwh,
            SUM(CASE WHEN hostname = 'sub-pc' THEN pc_kwh ELSE 0.0 END) AS sub_pc_kwh
          FROM
            pc_30m
          GROUP BY
            time_30m
        ),
        household_30m AS (
          SELECT
            time_30m,
            usage_kwh AS household_kwh
          FROM (
            SELECT
              TIMESTAMP_SUB(timestamp, INTERVAL 9 HOUR) AS time_30m,
              usage_kwh
            FROM
              `{household_table_id}`
          )
          WHERE
            time_30m >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        ),

        joined_data AS (
          SELECT
            h.time_30m,
            h.household_kwh,
            COALESCE(p.pc_total_kwh, 0.0) AS pc_total_kwh,
            COALESCE(p.main_pc_kwh, 0.0) AS main_pc_kwh,
            COALESCE(p.sub_pc_kwh, 0.0) AS sub_pc_kwh,
            GREATEST(0.0, h.household_kwh - COALESCE(p.pc_total_kwh, 0.0)) AS non_pc_kwh
          FROM
            household_30m h
          LEFT JOIN
            pc_pivot p ON h.time_30m = p.time_30m
        ),
        baseload_calc AS (
          SELECT
            PERCENTILE_CONT(non_pc_kwh, 0.15) OVER() AS estimated_baseload
          FROM
            joined_data
          LIMIT 1
        ),
        analyzed_30m AS (
          SELECT
            j.*,
            b.estimated_baseload,
            GREATEST(0.0, j.non_pc_kwh - b.estimated_baseload) AS active_appliances_kwh
          FROM
            joined_data j
          CROSS JOIN
            baseload_calc b
        ),
        daily_summary AS (
          SELECT
            FORMAT_DATE('%Y-%m-%d', DATE(time_30m, 'Asia/Tokyo')) AS date_jst,
            SUM(household_kwh) AS daily_household_kwh,
            SUM(pc_total_kwh) AS daily_pc_kwh,
            SUM(active_appliances_kwh) AS daily_active_kwh
          FROM
            analyzed_30m
          GROUP BY
            date_jst
        ),
        hourly_summary AS (
          SELECT
            EXTRACT(HOUR FROM time_30m AT TIME ZONE 'Asia/Tokyo') AS hour_jst,
            AVG(household_kwh) AS hourly_household_kwh,
            AVG(pc_total_kwh) AS hourly_pc_kwh,
            AVG(active_appliances_kwh) AS hourly_active_kwh
          FROM
            analyzed_30m
          GROUP BY
            hour_jst
        )
        SELECT
          SUM(household_kwh) AS total_household_kwh,
          SUM(pc_total_kwh) AS total_pc_kwh,
          SUM(main_pc_kwh) AS total_main_pc_kwh,
          SUM(sub_pc_kwh) AS total_sub_pc_kwh,
          SUM(non_pc_kwh) AS total_non_pc_kwh,
          SUM(estimated_baseload) AS total_baseload_kwh,
          SUM(active_appliances_kwh) AS total_active_appliances_kwh,
          AVG(household_kwh) AS avg_household_kwh,
          AVG(pc_total_kwh) AS avg_pc_kwh,
          ANY_VALUE(estimated_baseload) AS single_baseload_kwh,
          CORR(pc_total_kwh, active_appliances_kwh) AS correlation_pc_vs_active,
          CORR(pc_total_kwh, household_kwh) AS correlation_pc_vs_household,
          (SELECT ARRAY_AGG(STRUCT(date_jst, daily_household_kwh, daily_pc_kwh, daily_active_kwh) ORDER BY date_jst ASC) FROM daily_summary) AS daily_stats,
          (SELECT ARRAY_AGG(STRUCT(hour_jst, hourly_household_kwh, hourly_pc_kwh, hourly_active_kwh) ORDER BY hour_jst ASC) FROM hourly_summary) AS hourly_stats
        FROM
          analyzed_30m
        """

        bq_client = bigquery.Client(project=settings.PROJECT_ID)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("days", "INT64", days)
            ]
        )
        query_job = bq_client.query(query, job_config=job_config)
        
        # 非同期スレッド実行にラッピング
        results = await asyncio.to_thread(query_job.result)
        
        for row in results:
            return dict(row)
        
        raise RuntimeError("No data returned from BigQuery power aggregation query.")

    async def generate_finops_audit_report(self, data: dict, days: int = 7) -> str:
        """
        集計結果データをもとに、Gemini APIを呼び出して週次FinOps監査レポートを生成する。
        """
        genai.configure(api_key=settings.GEMINI_API_KEY)
        unit_price = settings.ELECTRICITY_UNIT_PRICE
        
        total_house_kwh = data.get("total_household_kwh") or 0.0
        total_pc_kwh = data.get("total_pc_kwh") or 0.0
        total_main_pc_kwh = data.get("total_main_pc_kwh") or 0.0
        total_sub_pc_kwh = data.get("total_sub_pc_kwh") or 0.0
        total_baseload_kwh = data.get("total_baseload_kwh") or 0.0
        total_active_kwh = data.get("total_active_appliances_kwh") or 0.0
        
        cost_house = total_house_kwh * unit_price
        cost_pc = total_pc_kwh * unit_price
        cost_main = total_main_pc_kwh * unit_price
        cost_sub = total_sub_pc_kwh * unit_price
        cost_baseload = total_baseload_kwh * unit_price
        cost_active = total_active_kwh * unit_price
        
        corr_active = data.get("correlation_pc_vs_active")
        corr_house = data.get("correlation_pc_vs_household")
        
        daily_stats_str = ""
        for day in (data.get("daily_stats") or []):
            daily_stats_str += f"- {day['date_jst']}: 家庭全体 {day['daily_household_kwh']:.2f} kWh, PC {day['daily_pc_kwh']:.2f} kWh, 空調/アクティブ {day['daily_active_kwh']:.2f} kWh\n"
            
        hourly_stats_str = ""
        for hr in (data.get("hourly_stats") or []):
            hourly_stats_str += f"- {hr['hour_jst']:02d}:00: 家庭平均 {hr['hourly_household_kwh']:.3f} kWh, PC平均 {hr['hourly_pc_kwh']:.3f} kWh, 空調/アクティブ平均 {hr['hourly_active_kwh']:.3f} kWh\n"

        prompt = f"""
あなたは非常に優秀なホームFinOpsの専門家およびエネルギーアナリストです。
提供された以下の世帯電力データ（直近 {days} 日間）を分析し、家庭全体の電力消費を最適化し、特にPCとエアコン（空調）の無駄を排除するための具体的な監査レポートを作成してください。

### 集計データ（直近 {days} 日間）
- 家庭全体消費電力量: {total_house_kwh:.2f} kWh (推定電気代: {cost_house:,.0f} 円)
- PC全体の総消費電力量: {total_pc_kwh:.2f} kWh (推定電気代: {cost_pc:,.0f} 円、全体の {total_pc_kwh/total_house_kwh*100 if total_house_kwh > 0 else 0:.1f}%)
  - メインPC (main-pc): {total_main_pc_kwh:.2f} kWh (推定電気代: {cost_main:,.0f} 円)
  - サブPC (sub-pc): {total_sub_pc_kwh:.2f} kWh (推定電気代: {cost_sub:,.0f} 円)
- 推定ベースロード（冷蔵庫・待機電力など。15%値ベースの推計値）: {total_baseload_kwh:.2f} kWh (推定電気代: {cost_baseload:,.0f} 円)
- 推定空調・アクティブ家電消費電力（ベースロード超過分）: {total_active_kwh:.2f} kWh (推定電気代: {cost_active:,.0f} 円)

### 相関分析結果
- PC合計消費電力 と 推定空調・アクティブ家電消費電力 の相関係数: {f"{corr_active:.4f}" if corr_active is not None else 'N/A'}
- PC合計消費電力 と 家庭全体消費電力 の相関係数: {f"{corr_house:.4f}" if corr_house is not None else 'N/A'}

### 日別消費推移
{daily_stats_str}

### 24時間帯別の平均消費パターン
{hourly_stats_str}

### レポートの要件：
1. **エグゼクティブサマリー**:
   - 今週の総電気料金の内訳と、無駄の主な要因（PC、空調、待機電力のどこに問題があるか）を総括してください。
2. **PCと空調の相関分析**:
   - 相関係数の値をもとに、PCの負荷（および排熱）がエアコンの消費電力に与えた影響を統計的・論理的に解説してください。
   - 相関係数が高い（例えば0.4以上）場合はPC排熱によるエアコン負荷への影響を指摘し、低い場合は別の主要因（時間帯や人間の活動など）が支配的であることを指摘してください。
3. **曜日別・時間帯別の消費パターン分析**:
   - 深夜や不在時の無駄な消費電力（PCがアイドル状態で起動しっぱなしになっていないか、深夜にエアコンやアクティブ家電の消費が高い状態が続いていないかなど）を具体的に特定し、指摘してください。
4. **具体的なFinOpsアクションプラン**:
   - 来週からすぐに実行できる、期待削減金額（電気代単価 {unit_price} 円/kWh）付きのアクションプランを3〜4個提案してください。
   
### フォーマットガイドライン：
- Discord上に投稿するのに適した、見出し、箇条書き、表、絵文字（💡, 💻, ❄️, 💸, 📈など）を効果的に使った視覚的に美しいマークダウン形式にしてください。
- 読者がすぐに理解でき、モチベーションが高まるトーン（事実に基づきつつ建設的で親しみやすいトーン）にしてください。
- Discordの1メッセージの制限（2000文字）に絶対に収まるように、要点を簡潔にまとめて1500〜1800文字程度にしてください。
"""

        model = genai.GenerativeModel("gemini-2.5-flash")
        response = await asyncio.to_thread(
            model.generate_content,
            prompt
        )
        return response.text

    async def perform_weekly_audit(self, days: int = 7) -> str:
        """
        週次FinOps監査を実行し、結果をDiscordに送信する。
        """
        try:
            logger.info("週次FinOps監査を開始します。")
            
            # 1. データの取得と相関分析
            data = await self.get_weekly_power_data(days=days)
            
            # 2. Gemini APIによるレポート生成
            report = await self.generate_finops_audit_report(data, days=days)
            
            # 3. Discordに送信
            if len(report) <= 2000:
                await discord_service.send_message(
                    settings.DISCORD_FINOPS_CHANNEL_ID,
                    report
                )
            else:
                parts = []
                current_part = ""
                for line in report.split("\n"):
                    if len(current_part) + len(line) + 1 > 1950:
                        parts.append(current_part)
                        current_part = line
                    else:
                        current_part = current_part + "\n" + line if current_part else line
                if current_part:
                    parts.append(current_part)
                
                for idx, part in enumerate(parts):
                    header = f"📊 **FinOps 監査レポート (パート {idx+1}/{len(parts)})**\n" if len(parts) > 1 else ""
                    await discord_service.send_message(
                        settings.DISCORD_FINOPS_CHANNEL_ID,
                        header + part
                    )
            
            logger.info("週次FinOps監査が正常に完了し、Discordへ送信されました。")
            return report
            
        except Exception as e:
            error_msg = (
                f"❌ **FinOps 監査エラー**\n"
                f"週次監査処理の実行中に例外が発生しました。\n"
                f"```\n{e}\n```"
            )
            logger.error(error_msg)
            await discord_service.send_message(
                settings.DISCORD_FINOPS_CHANNEL_ID,
                error_msg
            )
            raise e


finops_service = FinOpsService()
