#!/bin/bash
# SOCKS tunnel via uwuntu (read-only: no writes on uwuntu, just a tunnel).
set -e
HOST="${UWUNTU_HOST:-100.85.92.37}"
USER="${UWUNTU_USER:-panther}"
PORT="${TUNNEL_PORT:-1080}"
pkill -f "ssh.*-D ${PORT}" 2>/dev/null || true
ssh -o ExitOnForwardFailure=yes -o ConnectTimeout=15 -f -N -D "${PORT}" "${USER}@${HOST}"
sleep 2
echo "egress IP via uwuntu:"
curl -s --max-time 20 --socks5-hostname "127.0.0.1:${PORT}" https://api.ipify.org
echo
