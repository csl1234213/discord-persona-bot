# Discord Persona Bot

依據核實資料，在 Discord 模擬人物語氣。

由你自行安裝、設定和維護。人物資料、聊天記錄和機器人密鑰，都由你保管。

> AI 模擬語氣，並非本人。安全驗收尚未完成，請先在獨立測試伺服器試用。

## 開始了解

| 功能 | 用途 |
| --- | --- |
| 日常聊天 | 在指定頻道直接聊天，無需提及機器人 |
| 建立人物 | 加入獲准資料，核實後再發布 |
| 調整語氣 | 調整說話方式，不改資料範圍 |
| 重新聊天 | 結束目前對話，保留舊記錄 |
| 刪除資料 | 刪除自己在指定頻道的本地會話 |
| 自行管理 | 由建立者管理人物、資料與版本 |

## 如何聊天

在指定頻道，直接發訊息或提及機器人即可聊天。在其他頻道提及它，只會收到前往聊天專區的提示。私人訊息、其他伺服器、其他機器人及討論串的訊息不會觸發聊天。

聊天頻道以 `DISCORD_CHAT_CHANNEL_ID` 設定，不看頻道名稱。

> 回覆不代表人物本人的想法或目前立場。

人物回答以建立者提供並核實的資料為依據。語氣設定不能增加不存在的事實。資料不足時，應說明不知道，不補寫人物經歷。聊天內容不會自動加入人物資料，也不會自動用來訓練。

## 自行維護

你需要自行處理以下事項；本專案不包含代管服務。

| 項目 | 你需要做的事 |
| --- | --- |
| 保持運行 | 保持執行機器人的電腦與網絡可用 |
| 管理密鑰 | 私下保存密鑰，外洩時立即更換 |
| 更新資料 | 只加入獲准內容，發布前逐條核實 |
| 保管備份 | 更新前備份，依私隱政策處理舊副本 |
| 更新程式 | 先在測試環境檢查，再安排更新 |
| 查看狀態 | 遇到問題先查看狀態與本機記錄 |

不要把密鑰、私人文字、錄音、聊天資料或備份上傳 GitHub。遇到錯誤時，也不要公開未遮蓋的設定或記錄。

技術安裝指令見下文。已整理的發行安裝說明見 [release/README.md](release/README.md)；目前仍未通過完整發行驗收，不能視為正式上線保證。

## 運作方式

```text
收到指定頻道的訊息
  → 檢查使用範圍、頻率與可用額度
  → 找出目前人物及這次對話使用的版本
  → 比對已核實資料
  → 依模式回傳原話、生成回答或說明不知道
  → 檢查回覆，避免不受控地提及其他人
```

一般使用者不需要修改程式。維護者若要查閱實作：Discord 收發位於 `bot/`，人物、資料與模型處理位於 `app/`。不同回答模式的資料門檻不同；不要把測試模式當成正式安全驗收。

## 使用方法

日常使用：

- `/chat message:<text> private:<bool=false> evidence:<bool=false>`
- `/chat-reset`
- `/chat-erase`
- `/chat-settings`
- `/persona-info`
- `/help`

建立者用：

- `/persona create|list|select|show|clone-version|publish|activate|rollback|export`
- `/utterance add|import|list|show|verify|unverify|edit|delete|analyze`
- `/style show|edit`
- `/personality show|edit`
- `/address add|list|edit|delete`
- `/bot-status`
- `/bot-sync-commands`（只同步到指定伺服器）
- `/bot-reload-persona`
- `/bot-db-status`

管理指令只供設定中的建立者使用。伺服器管理員不會因此自動取得人物管理權限。一般使用者可聊天、重新聊天、刪除自己的本地會話、調整個人語氣及查看人物資料。

`/chat-reset` 保留舊記錄；`/chat-erase` 才會刪除指定伺服器與頻道中自己的本地會話。它不會刪除 Discord 原訊息、人物資料或短期安全額度。詳細範圍見 [私隱說明](PRIVACY.md)。

## 機器設定

1. 在 [Discord Developer Portal](https://discord.com/developers/applications) 建立自己的應用程式，進入 **Bot** 頁面設定機器人。
2. 私下保存 Bot Token。它相當於機器人的密碼，不要傳到聊天、截圖或 GitHub。
3. 在 **Bot > Privileged Gateway Intents** 開啟 **Message Content Intent**，讓機器人能讀取有權查看的普通訊息。不要開啟不需要的成員或上線狀態讀取權限。
4. 在 **OAuth2 > URL Generator** 選擇 `bot` 和 `applications.commands`，產生邀請連結。
5. 只允許查看頻道、發送訊息、讀取歷史及嵌入連結。需要傳送檔案時才另開附件權限。不要授予 **Administrator**，也要檢查頻道是否額外給了管理權限。
6. 設定自己的伺服器、聊天頻道及建立者使用者 ID。只在獨立測試伺服器完成檢查後，才安排正式使用。

`DISCORD_GUILD_ID`、`DISCORD_CHAT_CHANNEL_ID` 和 `DISCORD_OWNER_USER_IDS` 必須填寫。程式內也要設定 `DISCORD_ENABLE_MESSAGE_CONTENT_INTENT=true`；只在網站開啟並不足夠。缺少必要設定會停止啟動；連線後還會核對伺服器、文字頻道與可用人物。

## 開始安裝

以下是 Windows 維護者的本機安裝方式，需要 Python 3.11。請先開啟 PowerShell，進入你自己的專案資料夾。安裝路徑可自行選擇，以下不限定磁碟代號。

```powershell
Set-Location '<你的專案資料夾>'
New-Item -ItemType Directory -Force .cache\temp,.cache\pip,.cache\pycache | Out-Null
$env:TEMP = "$PWD\.cache\temp"
$env:TMP = "$PWD\.cache\temp"
$env:PIP_CACHE_DIR = "$PWD\.cache\pip"
$env:PYTHONPYCACHEPREFIX = "$PWD\.cache\pycache"
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Copy-Item .env.example .env
```

只在本機編輯 `.env`，不要上傳。至少填寫 `DISCORD_GUILD_ID`、`DISCORD_CHAT_CHANNEL_ID`、`DISCORD_OWNER_USER_IDS`，並開啟 `DISCORD_ENABLE_MESSAGE_CONTENT_INTENT`。

密鑰建議另存私密檔案，再用 `DISCORD_BOT_TOKEN_FILE` 指向它；不要同時設定 `DISCORD_BOT_TOKEN`。要測試發行安全設定，請使用 `compose.release.yml`，不要把本機開發設定當成發行驗收。

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m bot.main
```

預設 `LLM_PROVIDER=fake` 只供測試，不會呼叫真實模型。接入相容的模型服務時，才設定 `LLM_PROVIDER=openai_compatible`、`LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`。服務密鑰只留在本機私密設定。

發行安全模式還需要有限的回答長度及已核實的費用上限。詳見 `config.example.env`；即使是付費服務，也要在服務商網站另設使用額度。

只想檢查本機能否初始化，可使用不連線的測試：

```powershell
$env:BOT_SMOKE_TEST_MODE = 'true'
$env:DATABASE_URL = 'sqlite+aiosqlite:///./.cache/readme-smoke.sqlite3'
.\.venv\Scripts\python.exe -m bot.main
```

看到 `DISCORD_READY_SMOKE=true` 只代表本機初始化成功，不代表已連上 Discord。此方式不需要 Token，也不呼叫外部模型。完成後關閉這個 PowerShell 視窗，避免下一次仍沿用測試設定。

## 建立人物

建議順序：建立人物 → 加入獲准資料 → 查看預覽 → 核實內容 → 發布。

`/utterance analyze` 只提供建議，不會自行改寫或發布人物。每次對話使用建立時的人物版本；發布新版不會偷偷更換舊對話。需要重新開始時，使用 `/chat-reset`。

以下是進階維護操作，不是一般安裝步驟。請先備份，並在測試資料庫確認版本符合你的情況。

合併腳本會把 `0.2.0` 的資料與設定複製到 `0.3.0`，重新指向會話，最後刪除 `0.1.0` 和 `0.2.0`。操作失敗會撤回該次資料變更；不要未經確認就對正式資料執行：

```powershell
.\.venv\Scripts\python.exe .\scripts\consolidate_persona_versions.py --persona-name "<PERSONA_NAME>"
```

`/utterance import` 接受 UTF-8 的 `.jsonl` 或 `.txt`，會檢查檔案類型、大小、行數與重複內容。先預覽，再確認加入；預設為待核實，只有建立者明確選擇 `verify=true` 才標記為已核實。原附件不另行長期保存。

**目前提醒：** 合成 TXT 的預覽、確認匯入及待核實狀態已通過真人重測；其他檔案類型與完整安全驗收尚未完成。不要以正式人物資料試錯，也不要把單項通過當成可正式發布。

使用 `.jsonl` 時，每行可填寫以下內容：

```json
{"text":"獲准使用的文字","context":"前後文","topic":"主題","scene":"情境","verified":false}
```

維護者也可在明確指定測試資料庫後，使用本機匯入工具：

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_demo_persona
.\.venv\Scripts\python.exe -m scripts.import_utterances .\authorized.jsonl --persona-id <UUID> --version-id <UUID>
```

第二條指令預設不標記為已核實；確認來源和內容後才加上 `--verify`。這些工具會修改設定中的資料庫，不要拿正式資料測試。建立演示人物前，先完成上方資料庫更新指令。

追加工具會複製目前版本、保留原資料並跳過完全重複的行。注意：它會把新增內容直接標記為已核實、發布新版並更新既有會話。請先自行核實 TXT 全部內容並備份；失敗時會撤回該次資料變更：

```powershell
.\.venv\Scripts\python.exe .\scripts\append_persona_corpus.py .\append.txt --persona-name "<PERSONA_NAME>" --target-version 0.4.0
```

替換工具會複製目前版本，只修改符合條件的一筆文字，再發布新版。找不到、找到多筆或造成重複時，會撤回該次資料變更。請先備份並確認替換內容：

```powershell
.\.venv\Scripts\python.exe .\scripts\replace_persona_corpus.py --persona-name "<PERSONA_NAME>" --target-version 0.5.0 --remove-fragment "<TEXT_FRAGMENT>" --replacement-text "<REPLACEMENT_TEXT>"
```

## 資料管理

預設資料檔為 `data/persona_bot.db`。容器啟動前會執行 `alembic upgrade head`；本機安裝則需自行執行。以下資料表名稱只供維護者查閱：

- `personas`、`persona_versions`
- `utterances`、`vocabulary_rules`、`catchphrase_rules`、`address_rules`
- `style_profiles`、`personality_profiles`
- `discord_conversations`、`discord_message_records`
- `guild_configs`

對話歷史長度可用 `MAX_CONVERSATION_MESSAGES` 設定。發行安全模式以 `SECURITY_RETENTION_DAYS` 控制保留日數，預設 7 天，上限 30 天；不是無限保存。聊天記錄不會自動變成人物規則。

備份前先停止機器人。還原時先使用獨立測試位置；舊備份可能帶回已刪除的會話。備份與刪除例外見 [私隱說明](PRIVACY.md)，不要承諾能清除 Discord 或未知備份中的資料。

## 上線檢查

以下自動檢查不使用真實 Discord Token 或付費密鑰，不能代替真人在測試伺服器操作驗收：

```powershell
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pytest.exe -q
```

檢查工具使用專案內的 `.cache/`。執行前可沿用安裝段落的暫存設定。更新後先通過檢查，再安排測試；本專案目前仍未完成全部安全驗收。

## 容器安裝

以下為開發用的建置與本機初始化檢查，不是正式發行啟動方式：

```powershell
docker build -t discord-persona-bot:local .
docker run --rm -e BOT_SMOKE_TEST_MODE=true -e DATABASE_URL=sqlite+aiosqlite:///./data/smoke.db discord-persona-bot:local
```

要檢查發行安全設定，使用 `compose.release.yml`。先複製 `config.example.env` 為 `config.env`，填寫自己的設定；把 Token 私下保存為 `.secrets/discord_bot_token`，只允許自己的帳號讀取。不要同時在設定內填寫 `DISCORD_BOT_TOKEN`。

完成獨立測試設定後，先檢查設定，再更新資料庫與建立合成演示人物，最後啟動：

```powershell
docker compose -f compose.release.yml config --quiet
docker compose -f compose.release.yml build
docker compose -f compose.release.yml run --rm bot python -m scripts.seed_demo_persona
docker compose -f compose.release.yml up --no-build -d
docker compose -f compose.release.yml logs -f bot
```

演示人物只含合成文字。上方指令會在設定的資料庫寫入資料，只能對自己的測試環境執行；不要與正式環境共用設定或資料儲存位置。

發行設定以受限帳號執行、程式檔案唯讀，只有資料目錄可保存變更。健康檢查只確認資料庫可用；看到 `Bot ready discord_ready=true` 才代表該次 Discord 連線完成就緒。

Windows 可使用 Docker Desktop 的 Linux 容器模式。其他主機需要自行驗證相容性、可用空間及網絡；不要把本機測試成功當成另一台主機也通過。預設資料庫適合單一執行個體，不要讓多個機器人同時共用寫入。不要公開路由器管理頁或不必要的連接埠。

## 基本設定

本機開發模板見 `.env.example`；發行設定模板見 `config.example.env`。以下名稱需原樣填寫，不能改成中文：

- `MAX_IMPORT_BYTES`、`MAX_IMPORT_LINES`
- `MAX_CONVERSATION_MESSAGES`、`CHAT_RECENT_UTTERANCE_WINDOW`、`CONVERSATION_RETENTION_DAYS`
- `USER_CHAT_COOLDOWN_SECONDS`：同一使用者兩次聊天的等待時間。
- `MENTION_REDIRECT_COOLDOWN_SECONDS`：其他頻道導航提示的等待時間。
- `GUILD_COOLDOWN_SECONDS`：保留舊設定相容性，不作全伺服器共用拒絕限制。
- `MAX_CONCURRENT_CHAT_REQUESTS`：同時處理數量；還受等候數量與額度限制，不保證每筆都接受。
- `MAX_PENDING_CHAT_REQUESTS`：限制等候處理數量，避免無限累積。
- `MAX_CONCURRENT_LLM_CALLS`
- `DISCORD_SYNC_COMMANDS_ON_STARTUP`
- `BOT_SMOKE_TEST_MODE`

## 使用限制

- 只換同義字、不使用原文關鍵字時，仍可能找不到合適資料。
- 本機測試不能確認 Discord 權限、登入、實際回覆或另一台主機的狀況。
- 預設測試回答不代表真實模型的品質或安全表現。
- 等候數量或額度用完時，部分請求會被拒絕；不會無限排隊。
- 人物分析只給建議，不會自行發布。
- 預錄音訊預設關閉；發行版不提供語音頻道播放或語音輸入。
- 完整安全驗收仍未完成，詳見 [驗收狀態](release/customer/驗收狀態.md)。

## 私隱保護

只加入你有權使用的內容，發布前逐條核實。不要加入秘密、私人資料或未獲准的聊天記錄。機器人必須保留 AI 身份說明，不能冒充本人，也不能宣稱回覆是本人的立場。

GitHub 只放通用程式、測試與空白設定模板。真實人物文字、錄音、產生的音訊、資料庫、備份、記錄、Bot Token、模型密鑰及 Voice ID 都要留在專案之外。使用 `.gitignore` 與 `.dockerignore` 排除，發布前仍要另行檢查；私人儲存庫也不能當成密鑰保險箱。

預錄音訊預設關閉，而且發行設定固定關閉。需要啟用時，應另行設定及驗收；把獲准音訊放在主機私密目錄，只允許容器讀取，不加入發布檔案。相關設定名稱如下，不代表只填這三行就完成啟用：

```env
VOICE_ASSET_FEATURE_ENABLED=true
VOICE_ASSET_ROOT=/app/private/voice-assets
VOICE_BUTTON_TIMEOUT_SECONDS=300
```

只有回覆對應到已綁定的原話音訊時，才可提供「聼語音」按鈕。附件可見範圍、公開按鈕及過期行為需依啟用設定另行驗收，不承諾音訊永久有效。傳送預錄附件不需要加入語音頻道；人物音訊不得加入 Git 或容器映像。
