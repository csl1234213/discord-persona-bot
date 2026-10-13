# Privacy and data boundaries

The bot is an AI style simulation, not the real person represented by a persona.
Source-code licensing does not grant permission to distribute real-person text,
recordings or cloned voices. Private corpus/audio assets are not distributed.
Voice is disabled in the release profile; no TTS or STT provider is bundled.

SQLite stores persona configuration, verified reference statements and per-user
conversation records. Conversations are scoped by guild, channel, user and persona
version. A remote LLM receives the current message, recent conversation history,
abstract style/personality information and configured address/relationship labels.
It does not receive API keys as prompt content. Authentication headers go only to
the locally configured provider. No developer telemetry is configured.

`/chat-reset` deactivates the current session; it is NOT a deletion operation.
`/chat-erase` deletes this authenticated user's conversations and messages across
all personas/versions in the configured Guild + Channel. It cannot target another
user or channel. Corpus, voice assets and other users are not deleted. Erasure
serializes with in-flight chat, enables SQLite secure_delete, commits deletion and
checks WAL truncation. This is not a forensic SSD, Discord or remote-provider wipe.

Release mode runs cleanup at startup and every 300 seconds, including inactive
sessions: default 7 days, maximum 30. Global conversation/message admission limits
and a 256 MiB SQLite main-file cap prevent unrestricted chat storage growth.
Journal transient space can additionally approach the main-file cap. Maintenance
failure rejects further writes at capacity rather than promising successful cleanup.

Daily abuse/cost accounting retains only a SHA256-scoped actor key and counters,
not messages. These keys are pseudonymous, NOT anonymous. They persist after chat
erasure to prevent budget bypass, and expire after at most 72 hours plus one cleanup
interval. Admin replay IDs expire after an hour plus one interval. Fake release
provider does not cache prompts. Runtime logs must not contain chat bodies and are
size-rotated to three 10 MiB files; OS/container logs require operator retention care.

Backups are manual, private and encrypted by the operator. Keep at most three for
seven days, stop the bot before snapshot/restore, and destroy pre-erasure snapshots
when servicing an erasure request. Restoring old snapshots can resurrect erased
data and old quota counters; reapply erasures and retain current accounting before
resuming traffic. The application cannot erase unknown external backup copies,
Discord's original messages/attachments, provider logs or OS/SSD remnants.

Keep config, token files, SQLite volumes, backups and audit evidence private.
Token environment variables can appear in inspect/process diagnostics; prefer the
secret-file mechanism in the Linux-container release Compose.
