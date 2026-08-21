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

function startMockUpstream(port, state) {
  const server = http.createServer((req, res) => {
    if (req.method === 'POST' && req.url === '/chat/completions') {
      state.authorization = req.headers.authorization || '';
      let body = '';
      req.on('data', (chunk) => (body += chunk));
      req.on('end', () => {
        state.lastBody = JSON.parse(body);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          id: 'mock-chat',
          object: 'chat.completion',
          choices: [
            {
              index: 0,
              message: {
                role: 'assistant',
                content: 'Hello from the mock upstream.',
              },
              finish_reason: 'stop',
            },
          ],
        }));
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

test('chat completions validates inbound secret and uses server-side provider key', async (t) => {
  const upstreamPort = 8140;
  const appPort = 8141;
  const state = { authorization: '', lastBody: null };
  const upstream = await startMockUpstream(upstreamPort, state);
  t.after(() => upstream.close());

  const child = spawn(process.execPath, ['custom_llm.js'], {
    cwd: __dirname,
    env: {
      ...process.env,
      PORT: String(appPort),
      LLM_API_KEY: 'test-key',
      CUSTOM_LLM_INBOUND_SECRET: 'test-inbound-secret',
      LLM_BASE_URL: `http://127.0.0.1:${upstreamPort}`,
      THYMIA_ENABLED: 'false',
      SHEN_ENABLED: 'false',
      ENABLE_MEMORY: 'false',
    },
    stdio: 'ignore',
  });
  t.after(() => child.kill('SIGTERM'));

  await waitForReady(`http://127.0.0.1:${appPort}/ping`);

  const missingAuth = await fetch(`http://127.0.0.1:${appPort}/chat/completions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: 'gpt-5.4-mini',
      stream: false,
      messages: [{ role: 'user', content: 'Say hello.' }],
    }),
  });
  assert.equal(missingAuth.status, 401);

  const wrongAuth = await fetch(`http://127.0.0.1:${appPort}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: 'Bearer wrong-secret',
    },
    body: JSON.stringify({
      model: 'gpt-5.4-mini',
      stream: false,
      messages: [{ role: 'user', content: 'Say hello.' }],
    }),
  });
  assert.equal(wrongAuth.status, 401);

  const response = await fetch(`http://127.0.0.1:${appPort}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: 'Bearer test-inbound-secret',
    },
    body: JSON.stringify({
      model: 'gpt-5.4-mini',
      reasoning_effort: 'medium',
      stream: false,
      messages: [{ role: 'user', content: 'Say hello.' }],
    }),
  });

  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.match(payload.choices?.[0]?.message?.content || '', /mock upstream/i);
  assert.equal(state.authorization, 'Bearer test-key');
  assert.equal(state.lastBody?.reasoning_effort, 'medium');
});
