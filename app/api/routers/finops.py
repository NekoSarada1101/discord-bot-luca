import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

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
