#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="${MINDFIX_WORKSPACE_ROOT:-$(cd "$ROOT_DIR/.." && pwd)}"
MODE="${1:---check}"

FILES=(
  "agent-samples/simple-backend/core/agent.py"
  "agent-samples/simple-backend/core/config.py"
  "agent-samples/simple-backend/core/consultant_dashboard.py"
  "agent-samples/simple-backend/core/phone_numbers.py"
  "agent-samples/simple-backend/local_server.py"
  "agent-samples/simple-backend/tests/test_agent.py"
  "agent-samples/simple-backend/tests/test_auth_password_login.py"
  "agent-samples/simple-backend/tests/test_consultant_dashboard.py"
  "server-custom-llm/node/agent_speaker.js"
  "server-custom-llm/node/custom_llm.js"
  "server-custom-llm/node/memory_store.js"
  "server-custom-llm/node/integrations/thymia/thymia.js"
  "server-custom-llm/node/integrations/thymia/thymia_client.js"
  "server-custom-llm/node/rtm_client.js"
  "server-custom-llm/node/test_placeholder_bearer_smoke.js"
  "server-custom-llm/node/test_rag_completions_smoke.js"
  "server-custom-llm/node/test_rtm_client.js"
  "server-custom-llm/node/test_session_wrap_up_smoke.js"
)

case "$MODE" in
  --check|--apply) ;;
  *)
    echo "Usage: $0 [--check|--apply]" >&2
    exit 2
    ;;
esac

drift=0
for relative_path in "${FILES[@]}"; do
  source_path="$ROOT_DIR/runtime/$relative_path"
  target_path="$WORKSPACE_ROOT/$relative_path"
  if [[ ! -f "$source_path" ]]; then
    echo "missing private source: $source_path" >&2
    exit 1
  fi

  if [[ "$MODE" == "--apply" ]]; then
    mkdir -p "$(dirname "$target_path")"
    cp "$source_path" "$target_path"
    echo "applied $relative_path"
  elif [[ ! -f "$target_path" ]] || ! cmp -s "$source_path" "$target_path"; then
    echo "drift: $relative_path"
    drift=1
  fi
done

if [[ "$MODE" == "--check" && "$drift" -ne 0 ]]; then
  exit 1
fi

echo "private runtime overlays ${MODE#--} complete"
