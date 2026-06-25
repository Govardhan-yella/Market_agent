#!/usr/bin/env bash
# Market Agent Startup Script for macOS Login Items
# This script opens Terminal on login and runs the scheduler.
# This bypasses launchd TCC permissions errors while keeping the project on the Desktop.

cd "$(dirname "$0")"
echo "==========================================="
echo "Starting Indian Market Research Agent..."
echo "==========================================="
echo "Project Directory: $(pwd)"
echo ""

# Run the scheduler script
./scripts/start_scheduler.sh
