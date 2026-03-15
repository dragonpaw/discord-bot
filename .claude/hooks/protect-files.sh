#!/bin/bash
# PreToolUse hook: block edits to state files, BUILD_TAG, and .env

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [[ -z "$FILE_PATH" ]]; then
  exit 0
fi

# Normalize to relative path for pattern matching
REL_PATH="${FILE_PATH#"$CLAUDE_PROJECT_DIR"/}"

if [[ "$REL_PATH" =~ ^state/ ]] || \
   [[ "$REL_PATH" == "BUILD_TAG" ]] || \
   [[ "$REL_PATH" == ".env" ]] || \
   [[ "$REL_PATH" =~ ^\.env\. ]]; then
  echo "Cannot edit protected file: $REL_PATH" >&2
  exit 2
fi

exit 0
