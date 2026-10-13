set -euo pipefail
docker run --rm --user 1000:1000 --entrypoint sh nanobot:test -c 'test -w /app/.venv && test ! -w /app && test ! -w /app/nanobot && python -m scripts.install_channel_dependencies discord && python -c "import discord"'
