#!/bin/bash
# Liveness: the API server answers its own root endpoint.
#
# `/` is the one route that is registered without the access middleware, so this stays a
# liveness check and not an authentication test -- it keeps answering 200 while the
# credential pool is empty, which is exactly the state this service is placed in. bash's
# /dev/tcp because the image carries neither curl nor wget, and a script because the
# redirect would be three layers of quoting deep inside a unit value.
set -eu
exec 3<> "/dev/tcp/127.0.0.1/${CPA_CONTAINER_PORT}"
printf 'GET / HTTP/1.0\r\n\r\n' >&3
grep -q 'CLI Proxy API Server' <&3
