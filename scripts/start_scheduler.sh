#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
caffeinate -dimsu python3 -m market_agent.main scheduler
