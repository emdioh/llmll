#!/usr/bin/env bash
# Start (or restart) a local LanguageTool server on http://localhost:8010 for development
# and eval runs. Usage: scripts/languagetool.sh [stop]
source "$(dirname "$0")/_lib.sh"
require docker
name=llmll-languagetool
if [[ "${1:-}" == stop ]]; then
  docker rm -f "$name" >/dev/null 2>&1 && echo "stopped $name" || echo "$name was not running"
  exit 0
fi
if docker ps --format '{{.Names}}' | grep -qx "$name"; then
  echo "$name already running on http://localhost:8010"; exit 0
fi
docker rm -f "$name" >/dev/null 2>&1 || true
docker run -d --name "$name" -p 8010:8010 -e Java_Xms=512m -e Java_Xmx=1g erikvl87/languagetool >/dev/null
echo -n "waiting for LanguageTool"
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:8010/v2/languages" >/dev/null 2>&1; then echo " ready on http://localhost:8010"; exit 0; fi
  echo -n "."; sleep 1
done
echo -e "\nLanguageTool did not answer within 60 s; check: docker logs $name" >&2
exit 1
