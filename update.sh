#!/bin/bash
# Pull the latest code from GitHub, then restart only the services whose code changed.
# Web pages (web/) and cron scripts need no restart: they are read fresh each time.
set -e
cd "$(dirname "$0")"

declare -A SERVICE=([p1_logger.py]=p1-logger [loudness_logger.py]=loudness-logger
                    [room_logger.py]=room-logger [evohome_logger.py]=evohome-logger
                    [dashboard.py]=dashboard)

before=$(git rev-parse HEAD)
git pull --ff-only --quiet
changed=$(git diff --name-only "$before" HEAD)
if [ -z "$changed" ]; then echo "Already up to date."; exit 0; fi

git log --oneline "$before..HEAD"
for file in $changed; do
  service=${SERVICE[$file]}
  if [ -n "$service" ] && systemctl is-enabled --quiet "$service" 2>/dev/null; then
    sudo systemctl restart "$service" && echo "restarted $service"
  fi
  if [[ $file == *.service ]]; then
    echo "$file changed: sudo cp $file /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart ${file%.service}"
  fi
done
