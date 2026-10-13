set -euo pipefail
docker compose run --rm --no-deps --build -T nanobot-cli status
docker compose run --rm --no-deps -T --entrypoint sh nanobot-cli -s <<'OUTER'
set -eu
field() {
  awk -v key="$1:" '$1 == key { print $2 }' /proc/self/status
}
test "$(id -u)" = "0"
test "$(field NoNewPrivs)" = "1"
setpriv --reuid=nanobot --regid=nanobot --init-groups sh -s <<'INNER'
set -eu
field() {
  awk -v key="$1:" '$1 == key { print $2 }' /proc/self/status
}
test "$(id -u)" = "1000"
test "$(field NoNewPrivs)" = "1"
for capability_set in CapInh CapPrm CapEff CapAmb; do
  test "$(field "$capability_set")" = "0000000000000000"
done
INNER
OUTER
docker compose -f docker-compose.yml -f docker-compose.bwrap.yml --profile cli \
  config --format json > "${RUNNER_TEMP}/bwrap-compose.json"
python - <<'PY'
import json
import os
from pathlib import Path

config = json.loads(Path(os.environ["RUNNER_TEMP"], "bwrap-compose.json").read_text())
for service_name in ("nanobot-gateway", "nanobot-api", "nanobot-cli"):
    service = config["services"][service_name]
    assert {"CHOWN", "SETGID", "SETUID", "SYS_ADMIN"} <= set(service["cap_add"])
    assert "no-new-privileges:true" in service["security_opt"]
PY
