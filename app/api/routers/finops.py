import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.core.config import settings
from app.services.finops_service import finops_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/eventarc")
async def handle_gcs_event(request: Request, background_tasks: BackgroundTasks):
    """
    GCSへのファイル作成を検知したEventarcから叩かれるエンドポイント。
    ペイロードのパースのみ行い、ETL処理はサービス層へ委譲する。
    """
    body = await request.json()
    bucket_name = body.get("bucket")
    file_name = body.get("name")

    if not bucket_name or not file_name:
        bucket_name = request.headers.get("ce-bucket")
        file_name = request.headers.get("ce-subject")
        if file_name and file_name.startswith("objects/"):
            file_name = file_name.replace("objects/", "", 1)

    if not bucket_name or not file_name:
        raise HTTPException(status_code=400, detail="Bucket or file name missing.")

    background_tasks.add_task(
        finops_service.process_eneos_csv_and_notify,
        bucket_name,
        file_name,
    )

    return {"status": "accepted", "message": "ETL pipeline triggered."}


@router.get("/cron")
async def finops_cron(background_tasks: BackgroundTasks, authorization: str = Header(None)):
    """
    週次でFinOps監査を実行し、結果をDiscordへ通知するCron用エンドポイント
    """
    if authorization != f"Bearer {settings.CRON_SECRET}":
        raise HTTPException(status_code=403, detail="Unauthorized cron request")

    background_tasks.add_task(
        finops_service.perform_weekly_audit
    )

    return {"status": "accepted", "message": "FinOps weekly audit triggered."}

