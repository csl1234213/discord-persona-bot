# Persona Bot — pre-release inspection package

Status: RELEASE_BLOCKED. This is a local review artifact, not an approved release.
Source-code licensing is MIT; see the repository LICENSE. Private vulnerability
contact and independent release approval still require owner decisions.

Use Python 3.11 or Docker Engine with Compose. On Windows use Docker Desktop in
Linux-container mode. The code does not include a local LLM or real-person assets.
Do not use somebody else's Discord credentials, server IDs, corpus or recordings.

## Network-free demo

Run `docker compose -f compose.audit.yml build --no-cache`, then
`docker compose -f compose.audit.yml up -d`. Inspect logs with
`docker compose -f compose.audit.yml logs`. This isolated demo uses Fake LLM and
synthetic persona data, has no network, and does not connect to Discord.
Stop with `docker compose -f compose.audit.yml down`. The named data volume persists.
`down -v` permanently deletes that demo volume. Never use it on data you need.

## Own Discord installation (after release approval)

Create your own Discord Application and Bot in the Developer Portal. Enable only
Message Content Intent in Portal; dedicated-channel ordinary text requires it.
No member/presence intent is required. Invite with bot/application-command scopes,
View Channel, Send Messages and required attachment permissions. Do not grant
Administrator. Static MP3 attachments do not require Connect/Speak permissions.
Voice Channel playback and STT are not implemented by this release.

Copy `config.example.env` to `config.env`, fill your own Guild ID, Chat Channel ID
and Owner user IDs. Create `.secrets/discord_bot_token` containing only your token.
Restrict this directory/file to your OS account. Linux containers read the file
at `/run/secrets/discord_bot_token`. Do not set DISCORD_BOT_TOKEN simultaneously.
Rotate tokens by replacing that file privately and recreating the container.

Run `docker compose -f compose.release.yml config --quiet`, build, and then
`docker compose -f compose.release.yml up -d`. Missing config or token fails closed.
The root filesystem is read-only and the named SQLite volume is the only persistent
write location. Seed the synthetic demo explicitly with
`docker compose -f compose.release.yml run --rm bot python -m scripts.seed_demo_persona`.
This preserves the entrypoint's migration-before-seed order. Do not bypass the
entrypoint for a fresh database: creating tables first leaves migrations unrecorded.
Use only material you have permission to import and distribute.

Fake LLM is the default. For an external provider configure the trusted endpoint,
model and API key locally. Never accept endpoint overrides from chat messages.
For host Ollama on Docker Desktop use `http://host.docker.internal:11434/v1`;
container localhost is the container itself. Linux host connectivity depends on
your network setup and has not been independently verified. Native Ollama mode
requires `LLM_OLLAMA_NATIVE=true`. Keep a finite output-token limit and timeout.
No author's machine or model is a fallback in this package.

Release mode requires a verified `SECURITY_PRICE_MICROS_PER_TOKEN` ceiling for
every real provider. The unit is millionths of USD per token; use 0 only for your
non-billed local service. Requests and conservative token/cost reservations have
both per-user and global UTC-day limits, persist across restarts, and are not
refunded after unknown provider failures. A generation reserves up to six calls
for compatibility retry/failover; correction reserves again. Actual invoices are
not measured: enforce a billing cap at your provider as well. Prompt bytes, output
tokens, total generation deadline, concurrency and pending queue are bounded.
Ingress admission covers deferral, typing, errors and final sends, not only model
generation. Duplicate events and a full pending queue are silently refused to avoid
reply amplification; navigation/error notices are additionally capped at 60/minute
per process. Normal admitted answers still use the persistent user/global budgets.

`/chat-reset` only starts a fresh session. `/chat-erase` removes your local
conversations/messages in the configured Guild/Channel across all persona versions.
See PRIVACY.md for retention, abuse counters, backups and external-system limits.

Stop the container before backing up SQLite, and preserve all database/WAL/SHM
files together. Restore only to a new isolated volume, then verify migrations
and record counts. Full backup/upgrade/rollback acceptance remains pending;
do not treat this instruction as evidence that those workflows were tested.
Upgrades require a backup and rebuild from the new allowlisted release package.
Rolling back code may require restoring the matching schema backup.

Errors should identify configuration keys only. Check private local logs rather
than publishing expanded `docker compose config`, `docker inspect` or `.env`.
Do not expose incoming HTTP ports: the bot uses outgoing Discord Gateway/API TLS.

Official references: https://docs.discord.com/developers/events/gateway and
https://docs.docker.com/compose/how-tos/use-secrets/.
