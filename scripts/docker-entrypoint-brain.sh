#!/bin/sh
# Named volumes mount as root; the app runs as uid 1000 and must own /data.
set -e
mkdir -p /data
if [ "$(id -u)" = "0" ]; then
  chown -R sprucer:sprucer /data
  exec setpriv --reuid=sprucer --regid=sprucer --init-groups -- "$@"
fi
exec "$@"
