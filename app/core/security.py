import hmac
import hashlib
from fastapi import Request, HTTPException
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

from app.core.config import settings


# --- Discord Verification ---
async def verify_discord_signature(request: Request):
    """
    Discordからのリクエストヘッダに含まれる署名を検証する。
    FastAPIのDependsとして各エンドポイントに注入して使用する。
    """
    signature = request.headers.get("X-Signature-Ed25519")
    timestamp = request.headers.get("X-Signature-Timestamp")

    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="Missing signature headers.")

    body = await request.body()
    try:
        verify_key = VerifyKey(bytes.fromhex(settings.DISCORD_PUBLIC_KEY))
        message = timestamp.encode() + body
        verify_key.verify(message, bytes.fromhex(signature))
    except BadSignatureError:
        raise HTTPException(status_code=401, detail="Invalid request signature.")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid signature format.")


# --- Twitch Verification ---
async def verify_twitch_signature(request: Request):
    """
    Twitch EventSubからのリクエスト署名を検証する(HMAC-SHA256)。
    """
    message_id = request.headers.get("Twitch-Eventsub-Message-Id")
    timestamp = request.headers.get("Twitch-Eventsub-Message-Timestamp")
    signature = request.headers.get("Twitch-Eventsub-Message-Signature")

    if not message_id or not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing Twitch signature headers.")

    body = await request.body()
    # Twitchの署名対象：message_id + timestamp + raw_body
    hmac_message = message_id.encode('utf-8') + timestamp.encode('utf-8') + body

    expected_signature = "sha256=" + hmac.new(
        settings.TWITCH_WEBHOOK_SECRET.encode('utf-8'),
        hmac_message,
        hashlib.sha256
    ).hexdigest()

    # タイミング攻撃を防ぐため、常に一定時間で比較する hmac.compare_digest を使用する
    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(status_code=401, detail="Invalid Twitch signature.")
