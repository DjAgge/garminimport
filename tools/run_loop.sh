#!/bin/sh
# Long-running scheduler: runs tools/run_daily.sh once a day at RUN_TIME
# (default 06:00) forever. Used by the garmin-scheduler service in
# docker-compose.yml so that Portainer only needs to deploy the stack.
set -e

RUN_TIME="${RUN_TIME:-06:00}"

while true; do
    target=$(date -d "$RUN_TIME" +%s)
    if [ "$target" -le "$(date +%s)" ]; then
        target=$(date -d "tomorrow $RUN_TIME" +%s)
    fi
    sleep $((target - $(date +%s)))
    echo "running the daily Garmin export"
    sh tools/run_daily.sh || echo "daily run failed, will retry tomorrow"
done
