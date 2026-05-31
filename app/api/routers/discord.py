from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from app.core.security import verify_discord_signature
from app.commands.handler import command_handler

router = APIRouter()


@router.post("/interactions", dependencies=[Depends(verify_discord_signature)])
async def discord_interactions(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    interaction_type = body.get("type")

    # Type 1: PING (DiscordからのURLエンドポイント検証)
    if interaction_type == 1:
        return {"type": 1}

    # Type 2: APPLICATION_COMMAND (スラッシュコマンドの実行)
    if interaction_type == 2:
        # 重い処理を BackgroundTasks に積む
        background_tasks.add_task(command_handler.execute_and_patch, body)

        # 3秒ルールを回避するため、即座に Type 5 (考え中...) を返す
        return {"type": 5}

    # それ以外のインタラクションは一旦弾く
    raise HTTPException(status_code=400, detail="Unhandled interaction type.")
