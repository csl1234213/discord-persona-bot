# Discord Persona Bot

这是一个直接运行在 Discord 内的严格证据人物语言模拟机器人。产品入口是 Slash Command、私信和明确 `@机器人`，没有 React 前端，也不以 FastAPI 作为产品入口。

> 这是基于所提供材料生成的 AI 语言风格模拟，并非本人，也不代表本人当前观点。

机器人只使用创建者提供并明确核验的言论。人格参数只调整语气，不能扩张事实边界；证据覆盖不足时不会调用 LLM，而是直接说明“现有言论不足以判断这个人会怎样回应这个问题”。Discord 对话不会被自动加入语料，也不会用于自动训练。

## 架构与安全边界

```text
Discord /chat、DM、@mention
  -> 权限、白名单、冷却与并发检查
  -> DiscordContextResolver（用户、角色、关系、称呼）
  -> PersonaChatService（自动跟随当前活动版本、人格合并）
  -> verified-only Hybrid Retrieval（BM25 + 中文短语/n-gram + 元数据排名融合）
  -> 关键词命中优先直接返回原语料，BM25/Hybrid 仅辅助同级候选排序
  -> 直出模式无关键词时有效阈值为 0，按本地意图从已验证语料随机回退并排除近期重复
  -> 独立相关性覆盖阈值（默认 0.05；非直出证据模式零匹配仍拒答）
  -> 类型化 Prompt -> provider-neutral async LLM
  -> 输出边界校验（最多重试一次）
  -> 只允许受控地 @当前请求用户；语料中的其他 mention 全部转义
```

Discord 适配器位于 `bot/`，领域服务、仓储、校验器和 LLM 客户端位于 `app/`。内部主键使用 UUID，Discord snowflake 以字符串保存。SQLite 适用于本地和小型单实例部署，仓储与 async SQLAlchemy 设计可迁移到 PostgreSQL。

## Discord 命令

普通用户：

- `/chat message:<text> private:<bool=false> evidence:<bool=false>`
- `/chat-reset`
- `/chat-settings`
- `/persona-info`
- `/help`

Owner / 管理：

- `/persona create|list|select|show|clone-version|publish|activate|rollback|export`
- `/utterance add|import|list|show|verify|unverify|edit|delete|analyze`
- `/style show|edit`
- `/personality show|edit`
- `/address add|list|edit|delete`
- `/bot-status`
- `/bot-sync-commands scope:<test-guild|global>`
- `/bot-reload-persona`
- `/bot-db-status`

人物创建、发布、语料导入/导出、全局命令同步和诊断是 Owner-only。服务器管理员只能选择已发布人物；普通用户只能对话、重置自己的会话、设置安全范围内的个人语气覆盖和查看披露/证据。所有权限均由命令检查器执行，不依赖隐藏 UI。

## Discord Developer Portal 设置

1. 在 [Discord Developer Portal](https://discord.com/developers/applications) 创建 Application，并在 **Bot** 页面添加机器人。
2. 重置并复制 Token，只把它保存在部署主机未提交的 `.env` 中。不要把 Token 发到聊天、命令行参数、镜像或 Git。
3. 默认保持 **Message Content Intent** 关闭。MVP 不需要 **Server Members Intent** 或 **Presence Intent**。
4. 在 **OAuth2 > URL Generator** 选择 `bot` 和 `applications.commands` scopes。
5. 只授予：View Channels、Send Messages、Send Messages in Threads（需要线程时）、Read Message History、Embed Links、Attach Files（仅导入/导出需要）。不要授予 Administrator。
6. 将 Application ID、测试服务器 ID、Owner 用户 ID 写入本机 `.env`。测试服务器同步近乎立即；发布时由 Owner 显式执行 `/bot-sync-commands scope:global`。

默认 Intents：`guilds=true`、`guild_messages=true`、`dm_messages=true`，`message_content=false`、`members=false`、`presences=false`。Discord 会在私信和明确提及机器人的消息中提供必要内容，因此默认模式不读取服务器全部普通消息。

如确需专用频道自动回复，必须同时完成以下三项：

1. Developer Portal 启用 Message Content Intent；
2. 设置 `DISCORD_ENABLE_MESSAGE_CONTENT_INTENT=true`；
3. 在 `DISCORD_AUTO_REPLY_CHANNEL_IDS` 写入明确的频道 ID 白名单。

即使开关已启用，非白名单普通消息仍会被忽略；机器人消息和 webhook 消息始终被忽略。`DISCORD_ALLOWED_GUILD_IDS` 可进一步限制允许使用机器人的服务器；留空表示不额外限制服务器。

## 本地安装与运行（Windows）

项目要求 Python 3.11+。以下 PowerShell 示例把虚拟环境、临时目录、字节码和工具缓存全部放在项目的 D 盘目录：

```powershell
Set-Location 'D:\项目\PythonProject1\discord-persona-bot'
New-Item -ItemType Directory -Force .cache\temp,.cache\pip,.cache\pycache | Out-Null
$env:TEMP = "$PWD\.cache\temp"
$env:TMP = "$PWD\.cache\temp"
$env:PIP_CACHE_DIR = "$PWD\.cache\pip"
$env:PYTHONPYCACHEPREFIX = "$PWD\.cache\pycache"
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Copy-Item .env.example .env
```

只在本机私下编辑 `.env`。最少需要 `DISCORD_BOT_TOKEN`、`DISCORD_APPLICATION_ID`、`DISCORD_TEST_GUILD_ID` 和 `DISCORD_OWNER_USER_IDS`。不要提交 `.env`。

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m bot.main
```

默认 `LLM_PROVIDER=fake` 不产生付费调用。使用 OpenAI-compatible 提供商时才设置 `LLM_PROVIDER=openai_compatible`、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`；密钥只通过环境变量读取。

无 Token 的完整本地启动冒烟：

```powershell
$env:BOT_SMOKE_TEST_MODE = 'true'
$env:DATABASE_URL = 'sqlite+aiosqlite:///./.cache/readme-smoke.sqlite3'
.\.venv\Scripts\python.exe -m bot.main
```

成功时输出 `DISCORD_READY_SMOKE=true` 和注册的顶级命令数，不连接 Discord，也不调用外部 LLM。

## 人物和语料工作流

推荐在 Discord 内完成：创建人物 -> 向草稿添加/导入授权言论 -> 预览 -> 明确核验 -> `/utterance analyze` 查看未发布建议 -> 发布。人物发布新版本后，已有会话和新会话都会自动跟随当前活动版本；`/chat-reset` 只结束调用者当前会话。

需要把历史版本统一归口时，先备份数据库，再运行原子化合并脚本。以下命令会从 `0.2.0` 完整复制语料、规则和人物配置到已发布的 `0.3.0`，验证语料数量及已核验语料，重定向全部会话，最后删除 `0.1.0` 和 `0.2.0`；任一步失败都会回滚事务：

```powershell
.\.venv\Scripts\python.exe .\scripts\consolidate_persona_versions.py --persona-name "<PERSONA_NAME>"
```

`/utterance import` 只接受 UTF-8 `.jsonl` 或 `.txt`，并执行配置的字节数、行数、MIME/扩展名和重复检查。默认导入为未核验，只有 Owner 显式选择 `verify=true` 才核验。附件读取后不持久保存。

JSONL 每行可以包含：

```json
{"text":"授权言论","context":"上下文","topic":"主题","scene":"场景","verified":false}
```

也可在明确指向开发数据库后使用离线脚本：

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_demo_persona
.\.venv\Scripts\python.exe -m scripts.import_utterances .\authorized.jsonl --persona-id <UUID> --version-id <UUID>
```

第二条命令默认不核验；只有确认来源授权后才添加 `--verify`。脚本会修改所配置的数据库，切勿指向生产数据库做试验。

需要在完全保留活动版本语料的前提下追加 UTF-8 TXT 时，先备份数据库，再使用版本化追加脚本。它会克隆活动版本、跳过完全相同的重复行、把新行标记为已核验、发布目标版本并重定向既有会话；任一步失败都会回滚：

```powershell
.\.venv\Scripts\python.exe .\scripts\append_persona_corpus.py .\append.txt --persona-name "<PERSONA_NAME>" --target-version 0.4.0
```

需要按明确要求替换一条已发布语料时，使用版本化替换脚本。它要求移除片段在活动版本中只命中一条，克隆活动版本后只修改目标版本中的该条文本，并发布新版本；零命中、多命中或目标文本重复都会整体回滚：

```powershell
.\.venv\Scripts\python.exe .\scripts\replace_persona_corpus.py --persona-name "<PERSONA_NAME>" --target-version 0.5.0 --remove-fragment "<TEXT_FRAGMENT>" --replacement-text "<REPLACEMENT_TEXT>"
```

## 数据库和迁移

默认数据库为 `data/persona_bot.db`。启动容器前会自动执行 `alembic upgrade head`；非容器部署应手动运行同一命令。主要表：

- `personas`、`persona_versions`
- `utterances`、`vocabulary_rules`、`catchphrase_rules`、`address_rules`
- `style_profiles`、`personality_profiles`
- `discord_conversations`、`discord_message_records`
- `guild_configs`

只存储有限消息历史（`MAX_HISTORY_MESSAGES`），保留期由 `CONVERSATION_RETENTION_DAYS` 配置。不会从聊天记录自动训练、抽取或发布人物规则。

## 测试和质量门禁

所有 Discord 网络和 LLM 调用在测试中使用替身；测试不会使用真实 Token 或付费 Key。

```powershell
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pytest.exe -q
```

工具缓存路径已经在 `pyproject.toml` 固定到 `.cache/`。运行前仍建议设置上面的 D 盘 `TEMP`、`TMP`、`PIP_CACHE_DIR` 和 `PYTHONPYCACHEPREFIX`。

## Docker

无 Token、无付费 Key 的构建和冒烟：

```powershell
docker build -t discord-persona-bot:local .
docker run --rm -e BOT_SMOKE_TEST_MODE=true -e DATABASE_URL=sqlite+aiosqlite:///./data/smoke.db discord-persona-bot:local
```

正式运行前在部署主机创建未提交的 `.env`，然后：

```powershell
docker compose up -d --build
docker compose logs -f discord-persona-bot
```

镜像使用非 root 用户，`/app/data` 由命名卷持久化，启动入口先运行迁移，Compose 使用 `unless-stopped` 和 20 秒优雅停止窗口。容器 healthcheck 验证数据库连通性；日志中的 `Discord gateway ready` 表示真实 Discord Gateway 已就绪。

Windows Docker Desktop 与普通 Linux Docker 主机均可使用上述 Compose。小型常开家用服务器/软路由仅建议在支持 64 位 Docker、可持久化卷、稳定 TLS/DNS 且至少约 256–512 MB 可用内存时运行；不要把路由管理界面或 Bot 端口暴露到公网。SQLite 只适合单容器实例，多实例应迁移到 PostgreSQL。

## 配置项

完整模板见 `.env.example`。常用安全/资源选项：

- `MAX_IMPORT_BYTES`、`MAX_IMPORT_LINES`
- `MAX_HISTORY_MESSAGES`、`CHAT_RECENT_UTTERANCE_WINDOW`、`CONVERSATION_RETENTION_DAYS`
- `USER_COOLDOWN_SECONDS`：按 Discord 用户独立限速，不会让同服其他用户互相阻塞
- `GUILD_COOLDOWN_SECONDS`：兼容旧配置保留，当前不再作为服务器全局拒绝桶
- `MAX_CONCURRENT_CHAT_REQUESTS`：总请求处理并发数，超出部分排队而不是拒绝，默认 8
- `MAX_CONCURRENT_LLM_CALLS`
- `DISCORD_SYNC_COMMANDS_ON_STARTUP`
- `BOT_SMOKE_TEST_MODE`

## 已知限制

- 当前 Hybrid Retrieval 完全在本地运行，融合 BM25、连续短语、中文 n-gram、场景元数据和近期重复降权；没有启用外部向量嵌入，因此纯同义改写覆盖能力仍有限，但检索排名不能绕过证据阈值。
- 本地自动测试和 smoke mode 不验证真实 Discord Token、Gateway 连通性、Portal 配置或全局命令传播；这些必须在测试服务器人工验收。
- Fake LLM 只用于确定性开发/测试，不代表外部模型质量。
- SQLite 面向单实例小规模部署，不支持横向多副本写入。
- 容器 healthcheck 证明进程可访问数据库；真实 Discord 就绪状态以结构化日志 `Discord gateway ready` 为准。
- 语言分析只生成建议，当前版本不会一键把建议自动写入/发布为规则。

## 隐私与授权

只导入你有权使用的材料，并在发布前逐条核验。不得导入秘密、私人事实或未经授权的聊天记录。机器人必须始终以 AI 风格模拟身份出现，不能冒充真人，也不能声称输出代表真人当前观点。

Git 仓库只保存通用源码、迁移、测试和空白配置模板。真实人物语料、原始录音、生成音频、SQLite 数据库、备份、日志、Discord Token、LLM API Key、Voice ID、SSH 密钥及生产 `.env` 必须保存在仓库外，并通过 `.gitignore` 与 `.dockerignore` 双重排除。即使使用 GitHub 私人仓库，也不得提交这些材料。

预录音频功能默认关闭。启用时将经授权的音频保存在部署主机的私有持久目录，并以只读方式挂载到容器：

```env
VOICE_ASSET_FEATURE_ENABLED=true
VOICE_ASSET_ROOT=/app/private/voice-assets
VOICE_BUTTON_TIMEOUT_SECONDS=300
```

Discord 回复只为当前 `DIRECT_UTTERANCE` 映射提供“聼語音”按钮；音频作为仅请求用户可见的附件发送，不加入 Docker 镜像，不进入 Git，也不需要 Voice Channel、FFmpeg、PyNaCl、Connect 或 Speak 权限。
