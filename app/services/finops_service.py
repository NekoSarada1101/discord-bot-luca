import asyncio
import csv
import io
import logging
import zoneinfo
from datetime import datetime, timezone

from google import genai
from google.cloud import bigquery, storage
from table2ascii import Alignment, PresetStyle
from table2ascii import table2ascii as t2a

from app.core.config import settings
from app.services.discord_service import discord_service

logger = logging.getLogger(__name__)


def _struct_value(struct, key: str, default: float = 0.0) -> float:
    """BigQueryのSTRUCT/Row(またはNone)からフィールドを安全に取り出す。"""
    if struct is None:
        return default
    try:
        value = struct[key]
    except (KeyError, TypeError):
        return default
    return default if value is None else value


def _yen(value: float) -> str:
    return f"{value:,.0f}"


def _build_cost_tables(
    *,
    total_house_kwh: float,
    cost_house: float,
    total_pc_kwh: float,
    cost_pc: float,
    total_baseload_kwh: float,
    cost_baseload: float,
    total_active_kwh: float,
    cost_active: float,
    host_rows: list[tuple[str, float, float]],
    unit_price: float,
) -> str:
    """
    Discordはmarkdownの表(`| |`)を描画できないため、table2asciiでコードブロックの
    アスキー表を組み立てる。Geminiには表を作らせず、この関数の出力をそのまま前に付ける。
    """

    def share(value: float) -> str:
        return f"{value / total_house_kwh * 100:.1f}%" if total_house_kwh > 0 else "N/A"

    overview_table = t2a(
        header=["項目", "kWh", "電気代(円)", "家庭全体比"],
        body=[
            ["家庭全体", f"{total_house_kwh:.2f}", _yen(cost_house), "100.0%"],
            ["PC合計(実測)", f"{total_pc_kwh:.2f}", _yen(cost_pc), share(total_pc_kwh)],
            ["ベースロード(推定)", f"{total_baseload_kwh:.2f}", _yen(cost_baseload), share(total_baseload_kwh)],
            ["アクティブ家電(推定)", f"{total_active_kwh:.2f}", _yen(cost_active), share(total_active_kwh)],
        ],
        alignments=[Alignment.LEFT, Alignment.RIGHT, Alignment.RIGHT, Alignment.RIGHT],
        style=PresetStyle.thin_box,
    )

    host_body = []
    for label, real_kwh, inferred_kwh in host_rows:
        pc_share = f"{real_kwh / total_pc_kwh * 100:.1f}%" if total_pc_kwh > 0 else "N/A"
        coverage = f"{inferred_kwh / real_kwh * 100:.0f}%" if real_kwh > 0 else "N/A"
        host_body.append([
            label,
            f"{real_kwh:.2f}",
            _yen(real_kwh * unit_price),
            pc_share,
            f"{inferred_kwh:.2f}",
            coverage,
        ])

    host_table = t2a(
        header=["PC", "実測kWh", "電気代(円)", "PC内比率", "推論kWh", "捕捉率"],
        body=host_body,
        alignments=[Alignment.LEFT, Alignment.RIGHT, Alignment.RIGHT, Alignment.RIGHT, Alignment.RIGHT, Alignment.RIGHT],
        style=PresetStyle.thin_box,
    )

    return (
        "### 💰 コスト内訳サマリー\n"
        f"```\n{overview_table}\n```\n"
        "### 💻 PCホスト別内訳（実測 vs CPU+GPU推論）\n"
        f"```\n{host_table}\n```"
    )


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

    async def get_weekly_power_data(self, days: int = 7) -> dict:
        """
        BigQueryからPC消費電力(SwitchBot Plug Miniによる壁実測・15分値)と
        家庭消費電力量(30分値)を取得・結合し、相関分析・ベースロード推定等の集計結果を返す。
        CPU+GPU推論値(power_metrics)は、実測に対する捕捉率を示す参考値として別途集計する。
        """
        env_table_id = f"{settings.PROJECT_ID}.{settings.FINOPS_BQ_DATASET}.{settings.PC_ENV_METRICS_BQ_TABLE}"
        pc_table_id = f"{settings.PROJECT_ID}.{settings.FINOPS_BQ_DATASET}.{settings.PC_POWER_BQ_TABLE}"
        household_table_id = f"{settings.PROJECT_ID}.{settings.FINOPS_BQ_DATASET}.{settings.ENEOS_BQ_TABLE}"

        query = f"""
        WITH pc_env_raw AS (
          SELECT
            TIMESTAMP_SECONDS(DIV(UNIX_SECONDS(timestamp), 1800) * 1800) AS time_30m,
            plug_main_pc_w,
            plug_sub_pc_w
          FROM
            `{env_table_id}`
          WHERE
            timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        ),
        pc_env_30m AS (
          SELECT
            time_30m,
            AVG(plug_main_pc_w) * 0.5 / 1000.0 AS main_pc_kwh,
            AVG(plug_sub_pc_w) * 0.5 / 1000.0 AS sub_pc_kwh
          FROM
            pc_env_raw
          GROUP BY
            time_30m
        ),
        pc_pivot AS (
          SELECT
            time_30m,
            COALESCE(main_pc_kwh, 0.0) AS main_pc_kwh,
            COALESCE(sub_pc_kwh, 0.0) AS sub_pc_kwh,
            COALESCE(main_pc_kwh, 0.0) + COALESCE(sub_pc_kwh, 0.0) AS pc_total_kwh
          FROM
            pc_env_30m
        ),
        household_30m AS (
          SELECT
            time_30m,
            usage_kwh AS household_kwh
          FROM (
            SELECT
              timestamp AS time_30m,
              usage_kwh
            FROM
              `{household_table_id}`
          )
          WHERE
            time_30m >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        ),
        pc_by_host AS (
          SELECT
            SUM(p.main_pc_kwh) AS main_pc_total_kwh,
            SUM(p.sub_pc_kwh) AS sub_pc_total_kwh
          FROM
            pc_pivot p
          INNER JOIN
            household_30m h ON p.time_30m = h.time_30m
        ),
        joined_data AS (
          SELECT
            h.time_30m,
            h.household_kwh,
            COALESCE(p.pc_total_kwh, 0.0) AS pc_total_kwh,
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
        ),
        inferred_raw AS (
          SELECT
            TIMESTAMP_SECONDS(DIV(UNIX_SECONDS(timestamp), 1800) * 1800) AS time_30m,
            hostname,
            total_power_w
          FROM
            `{pc_table_id}`
          WHERE
            timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        ),
        inferred_30m AS (
          SELECT
            time_30m,
            hostname,
            AVG(total_power_w) * 0.5 / 1000.0 AS inferred_kwh
          FROM
            inferred_raw
          GROUP BY
            time_30m, hostname
        ),
        inferred_by_host AS (
          SELECT
            i.hostname,
            SUM(i.inferred_kwh) AS inferred_total_kwh
          FROM
            inferred_30m i
          INNER JOIN
            household_30m h ON i.time_30m = h.time_30m
          GROUP BY
            i.hostname
        )
        SELECT
          SUM(household_kwh) AS total_household_kwh,
          SUM(pc_total_kwh) AS total_pc_kwh,
          SUM(non_pc_kwh) AS total_non_pc_kwh,
          SUM(estimated_baseload) AS total_baseload_kwh,
          SUM(active_appliances_kwh) AS total_active_appliances_kwh,
          AVG(household_kwh) AS avg_household_kwh,
          AVG(pc_total_kwh) AS avg_pc_kwh,
          ANY_VALUE(estimated_baseload) AS single_baseload_kwh,
          CORR(pc_total_kwh, active_appliances_kwh) AS correlation_pc_vs_active,
          CORR(pc_total_kwh, household_kwh) AS correlation_pc_vs_household,
          (SELECT AS STRUCT main_pc_total_kwh, sub_pc_total_kwh FROM pc_by_host) AS pc_host_totals,
          (SELECT ARRAY_AGG(STRUCT(hostname, inferred_total_kwh)) FROM inferred_by_host) AS inferred_pc_host_stats,
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

    def _build_household_context(self) -> str:
        """
        レポート生成時にLLMへ渡す、家庭側の運用実態・制約条件を組み立てる。
        季節（実行月）に応じて空調の前提が変わる。
        """
        month = datetime.now(self.jst).month

        if month in (6, 7, 8, 9):
            season_context = (
                "- 現在は**夏季**。夜間も気温・湿度が高く、熱中症予防のため就寝中もエアコンを付けたまま寝ている。\n"
                "  深夜〜早朝の空調消費は「消し忘れ」ではなく、健康維持のために意図的に継続しているもの。\n"
                "- したがって「就寝時にエアコンを切る」「タイマーで途中停止する」という提案は**してはならない**。\n"
                "  夜間の空調は稼働を前提としたうえで、設定温度・風量・除湿モード・サーキュレーター併用・"
                "断熱/遮光などによる『同じ快適さをより少ない電力で得る』方向の提案に限定すること。"
            )
        elif month in (12, 1, 2, 3):
            season_context = (
                "- 現在は**冬季**。暖房を使用する時期であり、就寝中も室温維持のため暖房を使う場合がある。\n"
                "- 「暖房を切る」ではなく、設定温度・加湿併用・断熱などによる効率化の方向で提案すること。"
            )
        else:
            season_context = (
                "- 現在は**中間期（春/秋）**。空調の稼働は比較的少なく、"
                "空調消費が観測された場合は他のアクティブ家電の影響も併せて検討すること。"
            )

        return (
            "### 家庭の運用実態・制約条件（分析時に必ず考慮すること）\n"
            "- PCでは**BOINC（分散コンピューティングのボランティア計算）を24時間365日フル稼働**させている。\n"
            "  そのため深夜・不在時にPCの消費電力が高止まりしているのは**アイドル状態の消し忘れではなく、"
            "意図した計算処理の実行**である。\n"
            "- したがって「深夜にPCをシャットダウンする」「スリープ/休止状態に移行する」"
            "「使わない時間帯は電源を切る」といった、BOINCの稼働を止める提案は**してはならない**。\n"
            "  PCについては稼働継続を前提に、電力効率（ワットあたり演算性能）を高める方向"
            "（CPU/GPUの電力制限・アンダーボルト、BOINCの使用CPUコア数や実行率の調整、"
            "電気代の安い時間帯へのタスク寄せ、排熱処理の改善など）で提案すること。\n"
            "- PCは複数台あり、ホスト名別の内訳を提示しているが、**集計対象のPCはすべてBOINCを24時間稼働させている**。\n"
            "  したがって、どのマシンについても深夜・不在時に消費電力が高いのは正常な状態である。"
            "特定のマシンだけを「アイドル状態で無駄」「使っていないのに動いている」と判定してはならず、"
            "ホスト別の消費電力差は用途・構成・性能の違いによるものとして扱うこと。\n"
            f"{season_context}\n"
            "- 上記の前提を踏まえ、「無駄」と「意図的な固定コスト」を明確に区別して分析すること。\n"
            "  意図的な固定コストについては削減対象とせず、その金額を『納得して支払っているコスト』として"
            "可視化したうえで、効率改善の余地のみを論じること。\n"
        )

    async def generate_finops_audit_report(self, data: dict, days: int = 7) -> str:
        """
        集計結果データをもとに、Gemini APIを呼び出して週次FinOps監査レポートを生成する。
        """
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        unit_price = settings.ELECTRICITY_UNIT_PRICE
        
        total_house_kwh = data.get("total_household_kwh") or 0.0
        total_pc_kwh = data.get("total_pc_kwh") or 0.0
        total_baseload_kwh = data.get("total_baseload_kwh") or 0.0
        total_active_kwh = data.get("total_active_appliances_kwh") or 0.0

        cost_house = total_house_kwh * unit_price
        cost_pc = total_pc_kwh * unit_price
        cost_baseload = total_baseload_kwh * unit_price
        cost_active = total_active_kwh * unit_price

        corr_active = data.get("correlation_pc_vs_active")
        corr_house = data.get("correlation_pc_vs_household")

        # PC消費電力はSwitchBot Plug Miniの実測(env_metrics)を正として集計する。
        # ホストは固定2台（メインPC/サブPC）。CPU+GPU推論値(power_metrics)は
        # 実測に対する捕捉率を示す参考値として併記する。
        pc_host_totals = data.get("pc_host_totals")
        inferred_by_host = {
            row["hostname"]: (row["inferred_total_kwh"] or 0.0)
            for row in (data.get("inferred_pc_host_stats") or [])
        }
        host_defs = [
            ("DESKTOP-BS4B404", "メインPC", _struct_value(pc_host_totals, "main_pc_total_kwh")),
            ("boinc-server", "サブPC", _struct_value(pc_host_totals, "sub_pc_total_kwh")),
        ]

        pc_host_stats_str = ""
        for hostname, label, real_kwh in host_defs:
            share = real_kwh / total_pc_kwh * 100 if total_pc_kwh > 0 else 0
            inferred_kwh = inferred_by_host.get(hostname, 0.0)
            coverage_str = f"{inferred_kwh / real_kwh * 100:.0f}%" if real_kwh > 0 else "N/A"
            pc_host_stats_str += (
                f"  - {label}（{hostname}）: 実測(PlugMini) {real_kwh:.2f} kWh "
                f"(推定電気代: {real_kwh * unit_price:,.0f} 円、PC合計の {share:.1f}%)"
                f" ／ CPU+GPU推論値 {inferred_kwh:.2f} kWh（実測に対する捕捉率 {coverage_str}）\n"
            )

        cost_tables = _build_cost_tables(
            total_house_kwh=total_house_kwh,
            cost_house=cost_house,
            total_pc_kwh=total_pc_kwh,
            cost_pc=cost_pc,
            total_baseload_kwh=total_baseload_kwh,
            cost_baseload=cost_baseload,
            total_active_kwh=total_active_kwh,
            cost_active=cost_active,
            host_rows=[
                (label, real_kwh, inferred_by_host.get(hostname, 0.0))
                for hostname, label, real_kwh in host_defs
            ],
            unit_price=unit_price,
        )

        daily_stats_str = ""
        for day in (data.get("daily_stats") or []):
            daily_stats_str += f"- {day['date_jst']}: 家庭全体 {day['daily_household_kwh']:.2f} kWh, PC {day['daily_pc_kwh']:.2f} kWh, 空調/アクティブ {day['daily_active_kwh']:.2f} kWh\n"
            
        hourly_stats_str = ""
        for hr in (data.get("hourly_stats") or []):
            hourly_stats_str += f"- {hr['hour_jst']:02d}:00: 家庭平均 {hr['hourly_household_kwh']:.3f} kWh, PC平均 {hr['hourly_pc_kwh']:.3f} kWh, 空調/アクティブ平均 {hr['hourly_active_kwh']:.3f} kWh\n"

        household_context = self._build_household_context()

        prompt = f"""
あなたは非常に優秀なホームFinOpsの専門家およびエネルギーアナリストです。
提供された以下の世帯電力データ（直近 {days} 日間）を分析し、家庭全体の電力消費を最適化し、特にPCとエアコン（空調）の無駄を排除するための具体的な監査レポートを作成してください。

{household_context}
### 集計データ（直近 {days} 日間）
- 家庭全体消費電力量: {total_house_kwh:.2f} kWh (推定電気代: {cost_house:,.0f} 円)
- PC全体の総消費電力量: {total_pc_kwh:.2f} kWh (推定電気代: {cost_pc:,.0f} 円、全体の {total_pc_kwh/total_house_kwh*100 if total_house_kwh > 0 else 0:.1f}%)
  ※SwitchBot Plug Miniによる壁での実測値（env_metrics）を正の値として使用。参考として、CPU+GPUのみの推論値（power_metrics。マザボ・メモリ・ドライブ・ファン・NIC・PSU変換損失は含まない）が実測の何%を捕捉できているかも併記する。
{pc_host_stats_str}
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
0. **コスト内訳・ホスト別内訳の数値表はこちらのコード側で別途アスキー表として組み立てて先頭に表示済みです。あなたはこのデータの表を一切作らないでください**（Discordはmarkdownの表を描画できないため）。以下のセクションは、表の下に続く文章として書いてください。
1. **エグゼクティブサマリー**（表は使わず2〜3文の文章で）:
   - 今週の総電気料金の内訳を、「意図的な固定コスト（BOINCの24時間稼働、季節上必要な空調）」と「改善余地のあるコスト」に切り分けて総括してください。
   - どのマシンがBOINC稼働のコストをどれだけ占めているか（上に表示したホスト別の実測kWh・電気代）に文章で触れてください。
   - あわせて、CPU+GPU推論値が実測の何%しか捕捉できていないかにも触れ、差分（マザボ・メモリ・ドライブ・ファン・NIC・PSU変換損失など）がPC消費電力の相応の割合を占めていることを指摘してください。
2. **PCと空調の相関分析**:
   - 相関係数の値をもとに、PCの負荷（および排熱）がエアコンの消費電力に与えた影響を統計的・論理的に解説してください。
   - 相関係数が高い（例えば0.4以上）場合はPC排熱によるエアコン負荷への影響を指摘し、低い場合は別の主要因（時間帯や人間の活動など）が支配的であることを指摘してください。
   - BOINCが24時間稼働している前提で、PC排熱が空調負荷に転嫁されている度合いを評価してください。
3. **曜日別・時間帯別の消費パターン分析**:
   - 深夜帯のPC消費および空調消費は「上記の前提により意図的なもの」として扱い、消し忘れとして指摘しないでください。
   - そのうえで、前提では説明できない異常値（特定の日だけ突出している、想定より高い時間帯がある、稼働が想定外に途切れている等）に注目して指摘してください。
   - BOINC稼働分・夜間空調分がそれぞれ週あたり何円の固定コストになっているかを試算し、可視化してください。
4. **具体的なFinOpsアクションプラン**:
   - 来週からすぐに実行できる、期待削減金額（電気代単価 {unit_price} 円/kWh）付きのアクションプランを3〜4個提案してください。
   - **禁止事項**: PCのシャットダウン/スリープによるBOINC停止、就寝時のエアコン停止・タイマー切りを含む提案は絶対に含めないでください。
   - 代わりに、同じ稼働時間・同じ快適さを維持したまま消費電力を下げる施策（PCの電力効率チューニング、空調の設定・気流・断熱の最適化、料金プランや時間帯シフトの見直しなど）を提案してください。

### フォーマットガイドライン：
- **Discordはmarkdownの表（`| ヘッダー | ... |`形式）を描画できません。表(テーブル)は一切使わないでください。** 数値の一覧は見出し・箇条書き・太字を使った文章で表現してください。
- Discord上に投稿するのに適した、見出し、箇条書き、絵文字（💡, 💻, ❄️, 💸, 📈など）を効果的に使った視覚的に美しいマークダウン形式にしてください。
- 読者がすぐに理解でき、モチベーションが高まるトーン（事実に基づきつつ建設的で親しみやすいトーン）にしてください。
- 分量は以下の構造を厳守してください（Discordでは2000文字ごとに自動分割されるため、全体で3000文字を超えないこと）。
  - 前置き・自己紹介・締めの挨拶は書かず、いきなり本題（セクション1）から始める。
  - セクション1（サマリー）: 表を使わず3文以内の文章で。
  - セクション2（相関分析）: 相関係数の提示 ＋ 解説3文以内。
  - セクション3（パターン分析）: 箇条書き4項目以内、各項目2文以内。
  - セクション4（アクションプラン）: 4個以内。各プランは「施策」「根拠」「期待削減額」の3行のみで書き、それ以上の説明を足さない。
  - アクションプランの再掲や、末尾の総括文は作らない（同じ数値を2度提示しない）。
- 冗長な修飾・比喩・励ましの言い回しを削り、数値と根拠を優先してください。
"""

        response = await client.aio.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
        )
        return f"{cost_tables}\n\n{response.text.strip()}"

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
                in_code_block = False
                for line in report.split("\n"):
                    if line.strip().startswith("```"):
                        in_code_block = not in_code_block
                    # コードブロック(表)の途中ではフェンスが壊れるため分割しない
                    if not in_code_block and len(current_part) + len(line) + 1 > 1950:
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
