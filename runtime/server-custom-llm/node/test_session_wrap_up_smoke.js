const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const http = require('node:http');

async function waitForReady(url, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch (_error) {
      // retry
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`server at ${url} did not become ready`);
}

// Mock both the OpenAI chat endpoint and the Agora Speak endpoint so we can
// exercise /session-wrap-up end-to-end without touching real services.
function startMockUpstream(port, state) {
  const server = http.createServer((req, res) => {
    if (req.method === 'POST' && req.url === '/chat/completions') {
      let body = '';
      req.on('data', (chunk) => (body += chunk));
      req.on('end', () => {
        try {
          state.lastLlmBody = JSON.parse(body);
        } catch (_e) {
          state.lastLlmBody = null;
        }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(
          JSON.stringify({
            id: 'mock-wrap',
            object: 'chat.completion',
            choices: [
              {
                index: 0,
                message: {
                  role: 'assistant',
                  content:
                    'Thanks for talking today. We touched on your sleep and stress. How are you feeling right now? Take care.',
                },
                finish_reason: 'stop',
              },
            ],
          })
        );
      });
      return;
    }
    // Agora Speak endpoint: /{appId}/agents/{agentId}/speak
    if (req.method === 'POST' && /\/agents\/[^/]+\/speak$/.test(req.url || '')) {
      let body = '';
      req.on('data', (chunk) => (body += chunk));
      req.on('end', () => {
        try {
          state.lastSpeakBody = JSON.parse(body);
        } catch (_e) {
          state.lastSpeakBody = null;
        }
        state.speakCalls += 1;
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'ok' }));
      });
      return;
    }
    res.writeHead(404);
    res.end();
  });
  return new Promise((resolve) => {
    server.listen(port, '127.0.0.1', () => resolve(server));
  });
}

test('session-wrap-up returns closing text and calls Agora speak', async (t) => {
  const upstreamPort = 8136;
  const appPort = 8137;
  const state = { speakCalls: 0, lastSpeakBody: null, lastLlmBody: null };
  const upstream = await startMockUpstream(upstreamPort, state);
  t.after(() => upstream.close());

  const child = spawn(process.execPath, ['custom_llm.js'], {
    cwd: __dirname,
    env: {
      ...process.env,
      PORT: String(appPort),
      LLM_API_KEY: 'test-key',
      LLM_BASE_URL: `http://127.0.0.1:${upstreamPort}`,
      LLM_MODEL: 'gpt-5.5',
      LLM_REASONING_EFFORT: 'low',
      THYMIA_ENABLED: 'false',
      SHEN_ENABLED: 'false',
      ENABLE_MEMORY: 'false',
      AGENT_SERVER_SHARED_SECRET: 'test-agent-secret',
    },
    stdio: 'ignore',
  });
  t.after(() => child.kill('SIGTERM'));

  await waitForReady(`http://127.0.0.1:${appPort}/ping`);

  // Register a fake agent so /session-wrap-up can look it up.
  const registerResp = await fetch(`http://127.0.0.1:${appPort}/register-agent`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Agent-Server-Secret': 'test-agent-secret',
    },
    body: JSON.stringify({
      app_id: 'test-app',
      channel: 'test-channel',
      agent_id: 'agent-42',
      auth_header: 'Basic dGVzdA==',
      agent_endpoint: `http://127.0.0.1:${upstreamPort}`,
      max_session_duration: 0,
    }),
  });
  assert.equal(registerResp.status, 200, 'register-agent should succeed');

  const wrapResp = await fetch(`http://127.0.0.1:${appPort}/session-wrap-up`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Agent-Server-Secret': 'test-agent-secret',
    },
    body: JSON.stringify({
      app_id: 'test-app',
      channel: 'test-channel',
      user_id: '',
      agent_id: 'agent-42',
    }),
  });

  assert.equal(wrapResp.status, 200);
  const wrapBody = await wrapResp.json();
  assert.equal(wrapBody.success, true);
  assert.match(wrapBody.text, /talking today|How are you feeling/);
  assert.ok(wrapBody.estimated_duration_ms >= 3000, 'estimated_duration_ms should be at least 3s');
  assert.ok(wrapBody.estimated_duration_ms <= 30000, 'estimated_duration_ms should be capped at 30s');
  assert.equal(state.speakCalls, 1, 'Agora speak should be called exactly once');
  assert.equal(state.lastSpeakBody?.priority, 'APPEND');
  assert.match(state.lastSpeakBody?.text || '', /talking today|How are you feeling/);

  // System prompt should mention the End Call framing.
  const systemContent = state.lastLlmBody?.messages?.[0]?.content || '';
  assert.match(systemContent, /End Call|closing turn|wellbeing/i);
});

test('session-wrap-up returns 404 when no agent is registered', async (t) => {
  const upstreamPort = 8138;
  const appPort = 8139;
  const state = { speakCalls: 0, lastSpeakBody: null, lastLlmBody: null };
  const upstream = await startMockUpstream(upstreamPort, state);
  t.after(() => upstream.close());

  const child = spawn(process.execPath, ['custom_llm.js'], {
    cwd: __dirname,
    env: {
      ...process.env,
      PORT: String(appPort),
      LLM_API_KEY: 'test-key',
      LLM_BASE_URL: `http://127.0.0.1:${upstreamPort}`,
      THYMIA_ENABLED: 'false',
      SHEN_ENABLED: 'false',
      ENABLE_MEMORY: 'false',
      AGENT_SERVER_SHARED_SECRET: 'test-agent-secret',
    },
    stdio: 'ignore',
  });
  t.after(() => child.kill('SIGTERM'));

  await waitForReady(`http://127.0.0.1:${appPort}/ping`);

  const wrapResp = await fetch(`http://127.0.0.1:${appPort}/session-wrap-up`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Agent-Server-Secret': 'test-agent-secret',
    },
    body: JSON.stringify({ app_id: 'nope', channel: 'nope', agent_id: 'nope' }),
  });
  assert.equal(wrapResp.status, 404);
  assert.equal(state.speakCalls, 0);
});
