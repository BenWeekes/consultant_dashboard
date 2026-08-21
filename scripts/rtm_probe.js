#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

function loadRtmModule() {
  const explicitRoot = process.env.RTM_NODE_MODULE_ROOT;
  const candidates = [
    explicitRoot,
    path.resolve(__dirname, "../../server-custom-llm/node/node_modules"),
    path.resolve(__dirname, "../../../server-custom-llm/node/node_modules"),
  ].filter(Boolean);

  for (const candidate of candidates) {
    const target = path.join(candidate, "rtm-nodejs");
    if (fs.existsSync(target)) {
      return require(target);
    }
  }
  throw new Error(
    "Could not locate rtm-nodejs. Set RTM_NODE_MODULE_ROOT to a node_modules directory containing it."
  );
}

async function main() {
  const [
    appId,
    channel,
    token,
    uid,
    prompt,
    timeoutSeconds = "12",
    sendDelayMs = "1500",
    holdAfterResponseMs = "0",
  ] = process.argv.slice(2);

  if (!appId || !channel || !token || !uid || !prompt) {
    throw new Error(
      "Usage: rtm_probe.js <appId> <channel> <token> <uid> <prompt> [timeoutSeconds] [sendDelayMs]"
    );
  }

  const AgoraRTM = loadRtmModule();
  const client = new AgoraRTM.RTM(appId, uid, { token });
  const timeoutMs = Math.max(1000, Number(timeoutSeconds) * 1000 || 12000);
  const sendDelay = Math.max(0, Number(sendDelayMs) || 0);
  const holdMs = Math.max(0, Number(holdAfterResponseMs) || 0);
  const startMs = Date.now();
  let sentMs = 0;
  let settled = false;
  let responsePayload = null;

  const finish = async (code, payload) => {
    if (settled) return;
    settled = true;
    try {
      await client.unsubscribe(channel).catch(() => {});
    } catch (_) {}
    try {
      await client.logout().catch(() => {});
    } catch (_) {}
    process.stdout.write(`${JSON.stringify(payload)}\n`);
    process.exit(code);
  };

  client.addEventListener("message", async (event) => {
    try {
      const publisher = String(event.publisher || "");
      let text = "";
      if (typeof event.message === "string") {
        text = event.message;
      } else if (event.message instanceof Uint8Array) {
        text = new TextDecoder().decode(event.message);
      } else if (event.message instanceof ArrayBuffer) {
        text = new TextDecoder().decode(new Uint8Array(event.message));
      }
      if (!text || publisher === String(uid)) {
        return;
      }
      responsePayload = {
        ok: true,
        channel,
        publisher,
        sent_ms: sentMs,
        received_ms: Date.now(),
        latency_ms: sentMs ? Date.now() - sentMs : null,
        response: text,
      };
      if (holdMs > 0) {
        setTimeout(() => {
          finish(0, responsePayload);
        }, holdMs);
      } else {
        await finish(0, responsePayload);
      }
    } catch (error) {
      await finish(1, { ok: false, error: error.message || String(error) });
    }
  });

  await client.login();
  await client.subscribe(channel, { withMessage: true, withPresence: false });

  setTimeout(async () => {
    try {
      sentMs = Date.now();
      await client.publish(channel, prompt);
    } catch (error) {
      await finish(1, {
        ok: false,
        error: error.message || String(error),
        phase: "publish",
      });
    }
  }, sendDelay);

  setTimeout(async () => {
    await finish(1, {
      ok: false,
      error: `Timed out after ${timeoutMs}ms waiting for RTM response`,
      start_ms: startMs,
      sent_ms: sentMs || null,
    });
  }, timeoutMs);
}

main().catch((error) => {
  process.stdout.write(
    `${JSON.stringify({ ok: false, error: error.message || String(error) })}\n`
  );
  process.exit(1);
});
