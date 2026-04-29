#!/bin/bash
# Install licai (理财助手) as a macOS launchd service.
# Generates a personalized plist from the template and registers it with launchd.
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$DIR/com.licai.plist.example"
PLIST_NAME="com.licai.plist"
GENERATED="$DIR/$PLIST_NAME"
TARGET="$HOME/Library/LaunchAgents/$PLIST_NAME"

if [ ! -f "$TEMPLATE" ]; then
    echo "Template not found: $TEMPLATE"
    exit 1
fi

mkdir -p "$DIR/logs" "$DIR/backups"

# Substitute __PROJECT_PATH__ with the absolute project path
sed "s|__PROJECT_PATH__|$DIR|g" "$TEMPLATE" > "$GENERATED"

# Stop if already running
launchctl bootout "gui/$(id -u)" "$TARGET" 2>/dev/null || true

# Copy to LaunchAgents and load
cp "$GENERATED" "$TARGET"
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "Done! Service installed and started."
echo "  View logs: tail -f $DIR/logs/stderr.log"
echo "  Stop:      launchctl bootout gui/\$(id -u) $TARGET"
echo "  Start:     launchctl bootstrap gui/\$(id -u) $TARGET"
echo "  URL:       http://localhost:8888"
