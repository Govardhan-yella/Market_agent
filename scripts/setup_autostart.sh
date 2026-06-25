#!/usr/bin/env bash
set -euo pipefail

# Ensure target directories exist
mkdir -p "$HOME/Library/LaunchAgents"

# Get absolute path to the project directory
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_PATH="$HOME/Library/LaunchAgents/com.marketagent.scheduler.plist"

echo "Creating launchd configuration at: $PLIST_PATH"

cat <<'EOF' > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.marketagent.scheduler</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>__ROOT_DIR__/scripts/start_scheduler.sh</string>
    </array>
    
    <key>RunAtLoad</key>
    <true/>
    
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>
    
    <key>StandardOutPath</key>
    <string>__ROOT_DIR__/work/logs/scheduler_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>__ROOT_DIR__/work/logs/scheduler_stderr.log</string>
</dict>
</plist>
EOF

# Replace placeholder with absolute root path
sed -i '' "s|__ROOT_DIR__|$ROOT_DIR|g" "$PLIST_PATH"

echo "Registering and starting the Launch Agent..."

# Unload first if already loaded to avoid duplicates
launchctl unload "$PLIST_PATH" 2>/dev/null || true

# Load the new service
launchctl load "$PLIST_PATH"

echo "Successfully configured next-day restart."
echo "The agent will run on startup/login and at 08:30 IST daily."
echo "To check the logs, look at: $ROOT_DIR/work/logs/scheduler_stdout.log"
