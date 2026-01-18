#!/usr/bin/env bash
set -e

# Render sets PORT for web services
exec uvicorn slack_server:app --host 0.0.0.0 --port "${PORT:-3000}"