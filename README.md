# discord-bot

## ディレクトリ構成

```
.
├── app/
│   ├── main.py                 # アプリケーションのエントリーポイント
│   ├── core/                   # 基盤設定
│   │   └── security.py         # Discord(ED25519)やTwitch(HMAC)の署名検証ロジック
│   ├── api/                    # エンドポイント (リクエストの入り口)
│   │   ├── dependencies.py     # DI(依存性の注入)用コンポーネント
│   │   ├── routers/
│   │   │   ├── discord.py      # スラッシュコマンド用のInteraction Webhook
│   │   │   ├── twitch.py       # Twitch EventSub Webhook
│   │   │   └── youtube.py      # Cloud Schedulerから叩かれる定期実行トリガー
│   ├── services/               # ビジネスロジック (実際の処理)
│   │   ├── discord_service.py  # Discord APIへのリクエスト (メッセージ送信など)
│   │   ├── twitch_service.py   # Twitch APIとの通信
│   │   └── youtube_service.py  # YouTube RSSのパースと新着判定
│   ├── commands/               # Discordスラッシュコマンドの実装群
│   │   ├── __init__.py
│   │   ├── handler.py          # コマンドのルーティング制御
│   │   └── definitions/        # 各コマンドの具体的な処理
│   │       ├── notification.py
│   │       └── ...
│   └── models/                 # データモデル
│       ├── requests.py         # APIリクエストのスキーマ
│       └── domain.py           # 内部で引き回すデータ構造
├── Dockerfile                  # Cloud Runデプロイ用
├── requirements.txt            # (または pyproject.toml / uv.lock)
└── .env.example                # 必要な環境変数のリスト
```

## 設計方針

レイヤーごとに責務を分離し、トリガーや外部 API の変更がビジネスロジックへ波及しない構成にしています。

### `api/` — ルーティング層

- リクエストの受け付け、署名検証の呼び出し、ペイロードのパースのみを担当する
- ビジネスロジックは書かず、`services/` や `commands/` に委譲する
- Webhook や定期実行など、呼び出し元が変わっても下位レイヤーへの影響を抑えられる

### `core/security.py` — セキュリティ層

- Discord・Twitch から届く Webhook が正当なものかを検証する
- Discord Interactions API では ED25519 による署名検証が必須であり、ここを通過しないリクエストは処理しない
- 署名検証ロジックを一箇所に集約し、各ルーターから共通利用する

### `commands/` — コマンド管理層

- Discord スラッシュコマンドの定義と実行を集約する
- コマンド名と処理関数を `handler.py` でマッピングし、追加時は `definitions/` にファイルを足すだけで済む
- `main.py` や `api/routers/discord.py` の肥大化を防ぐ

### `services/` — サービス層

- YouTube RSS の取得・新着判定、Discord へのメッセージ送信など、実際の処理を担う
- Cloud Run はステートレスなため、通知済み状態などの永続化は Firestore と連携する
- 外部 API やストレージとの通信ロジックをここに集約する

## コマンド

### ローカル動作確認

エンドポイントの公開

```
ngrok http 8080
```

Botの起動

```
uvicorn app.main:app --host 127.0.0.1 --port 8080
```
