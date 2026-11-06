#!/bin/sh
# Daily job for the NAS: run the health export and push it into Budibase.
# Used as the container command in docker-compose.yml.
#
# The first run needs an interactive login (password + MFA):
#   docker compose run --rm garmin-export sh tools/run_daily.sh
# Every later run reuses the cached tokens (--tokenfile) without a login.
#
# For the initial history import fetch more days, e.g. the last 60:
#   HEALTH_DAYS=60 docker compose run --rm garmin-export sh tools/run_daily.sh
set -e

EXPORT_DIR="/data/export"
HEALTH_DAYS="${HEALTH_DAYS:-2}"

python3 gcexport.py \
    -c 1 -f gpx \
    --health "$HEALTH_DAYS" \
    --tokenfile /data/tokens.json \
    -d "$EXPORT_DIR" \
    "$@"

EXPORT_DIR="$EXPORT_DIR" python3 tools/budibase_import.py
