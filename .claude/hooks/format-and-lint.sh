#!/bin/bash
# PostToolUse hook: auto-format and fix lint errors after Edit/Write

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only process Python files
if [[ "$FILE_PATH" != *.py ]]; then
  exit 0
fi

# Only process files that exist (Write to a new path that failed, etc.)
if [[ ! -f "$FILE_PATH" ]]; then
  exit 0
fi

uv run ruff check --fix-only --quiet "$FILE_PATH" 2>/dev/null
uv run ruff format --quiet "$FILE_PATH" 2>/dev/null

exit 0
