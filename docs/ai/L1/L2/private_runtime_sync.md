# Private Runtime Sync

This project uses sibling runtime repos in local deployment:

- `agent-samples/simple-backend`
- `server-custom-llm`

For MindFix project work, the private source-of-truth record should also live in this repo.

This file mirrors the project-specific external runtime changes that were required for:

- consultant `AI Testing Mode`
- client `AI escalation enabled`
- full AI transcript retention for testing consultants
- suppression of live AI escalation when client AI escalation is disabled

## simple-backend change

File in sibling repo:

- `agent-samples/simple-backend/local_server.py`

Purpose:

- pass dashboard context flags through agent registration:
  - `consultant_ai_testing_mode`
  - `client_ai_escalation_enabled`

Relevant diff:

```python
register_payload.update({
    "client_id": dashboard_context.get("client_id", ""),
    "consultant_id": dashboard_context.get("consultant_id", ""),
    "consultant_name": dashboard_context.get("consultant_name", ""),
    "consultant_ai_testing_mode": bool(
        (dashboard_context.get("context") or {}).get("consultant_ai_testing_mode")
    ),
    "client_ai_escalation_enabled": bool(
        (dashboard_context.get("context") or {}).get("ai_escalation_enabled", True)
    ),
    "consultant_dashboard_url": constants.get("CONSULTANT_DASHBOARD_URL", ""),
    "consultant_dashboard_shared_secret": constants.get("CONSULTANT_DASHBOARD_INTERNAL_SHARED_SECRET", ""),
    "profile_name": constants.get("PROFILE_NAME", "default"),
})
```

## server-custom-llm changes

Files in sibling repo:

- `server-custom-llm/node/consultant_dashboard_client.js`
- `server-custom-llm/node/integrations/mindfix_crisis/mindfix_crisis.js`
- `server-custom-llm/node/memory_store.js`

### 1. Dashboard config fields

Purpose:

- make the runtime aware of:
  - consultant AI transcript-retention mode
  - client AI escalation policy

Relevant diff:

```javascript
return {
  baseUrl,
  sharedSecret,
  clientId,
  displayName: earlyParams.display_name || '',
  consultantId: earlyParams.consultant_id || '',
  consultantName: earlyParams.consultant_name || '',
  profileName: earlyParams.profile_name || 'default',
  meetingId: earlyParams.meeting_id || '',
  meetingMode: !!earlyParams.meeting_mode,
  meetingRuntimeKey: earlyParams.meeting_runtime_key || '',
  aiTestingMode: !!earlyParams.consultant_ai_testing_mode,
  clientAiEscalationEnabled: earlyParams.client_ai_escalation_enabled !== false,
};
```

### 2. Crisis-module suppression guard

Purpose:

- if `ai_escalation_enabled=false`, the runtime must not:
  - start escalation
  - enter pending-escalation state
  - inject escalation-underway system text

Relevant diff:

```javascript
function isClientEscalationEnabled(state) {
  return state?.dashboard?.clientAiEscalationEnabled !== false;
}

async onSafetyUpdate({ appId, channel, safety }) {
  const state = getOrCreateState(appId, channel);
  if (!CRISIS_CALL_ENABLED) return;
  if (!isAiHumanSession(state)) return;
  if (!isClientEscalationEnabled(state)) return;
  ...
}
```

### 3. AI transcript retention from conversation store

Purpose:

- when `consultant_ai_testing_mode=true`, retain full AI transcript for dashboard session review

Relevant diff:

```javascript
function buildAiTranscript(messages) {
  if (!Array.isArray(messages) || messages.length === 0) return null;
  const lines = [];
  for (const message of messages) {
    if (!message || typeof message !== 'object') continue;
    const role = message.role === 'assistant'
      ? 'Therapist'
      : message.role === 'user'
        ? 'Client'
        : null;
    if (!role) continue;
    const content = typeof message.content === 'string' ? message.content.trim() : '';
    if (!content) continue;
    lines.push({
      speaker: role,
      text: content,
      time: message.timestamp || message.created_at || '',
    });
  }
  if (!lines.length) return null;
  return {
    provider: 'conversation_store',
    text: lines.map((line) => `${line.speaker}: ${line.text}`).join('\\n'),
    lines,
  };
}

const transcript = state?.meetingMode
  ? getMeetingTranscript(runtimeKey || state.runtimeKey || '')
  : (state?.dashboard?.aiTestingMode ? buildAiTranscript(messages) : null);
```

## Operational note

These external runtime changes are intentionally mirrored here as project documentation because:

- the public sample repos should not be treated as the canonical MindFix project history
- the private `consultant_dashboard` repo is the correct long-term record for project-specific AI workflow decisions
