#!/bin/sh
set -e

mkdir -p /app/config /app/data
for f in /app/config.default/*; do
    name="$(basename "$f")"
    if [ ! -e "/app/config/$name" ]; then
        cp "$f" "/app/config/$name"
        echo "[entrypoint] seeded config/$name"
    fi
done

exec python -m app --host 0.0.0.0 --port "${PORT:-8000}" --no-browser "$@"
