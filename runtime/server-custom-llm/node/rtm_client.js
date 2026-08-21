/**
 * RTM (Real-Time Messaging) client for the Custom LLM Server.
 * Node.js only — uses the rtm-nodejs package.
 *
 * Manages one RTM session per channel. Each session has its own login
 * using the appId/uid/token from the ConvoAI request params. The simple-backend
 * generates channel-scoped RTM UIDs (e.g. "5001-{channel}") so each session
 * has a unique identity and won't kick other sessions off.
 */

const logger = {
  info: (message) => console.log(`INFO: [RTM] ${message}`),
  debug: (message) => console.log(`DEBUG: [RTM] ${message}`),
  error: (message, error) => console.error(`ERROR: [RTM] ${message}`, error),
  warn: (message) => console.warn(`WARN: [RTM] ${message}`),
};

// Insert connecting sessions immediately so register-agent and the first chat
// request cannot create duplicate RTM clients for the same channel.
const sessions = new Map();
let messageHandlers = [];
let presenceHandlers = [];
let rtmModuleFactory = () => require('rtm-nodejs');
const MAX_RECONNECT_ATTEMPTS = 10;
const BASE_RECONNECT_DELAY = 2000;
const MAX_RECONNECT_DELAY = 60000;

/**
 * Initialize RTM from environment variables (legacy). Returns the client or null.
 */
async function initRTM() {
  const appId = process.env.AGORA_APP_ID;
  const userId = process.env.AGORA_RTM_USER_ID;
  const token = process.env.AGORA_RTM_TOKEN || '';
  const channel = process.env.AGORA_RTM_CHANNEL;

  if (!appId || !userId || !channel) {
    logger.debug(
      'RTM env vars not set (AGORA_APP_ID, AGORA_RTM_USER_ID, AGORA_RTM_CHANNEL) — skipping RTM'
    );
    return null;
  }

  return initRTMWithParams(appId, userId, token, channel);
}

/**
 * Initialize RTM session for a channel. Creates a new session if one doesn't
 * exist for this channel. Idempotent — returns existing client if already connected.
 */
async function initRTMWithParams(appId, uid, token, channel) {
  if (!appId || !uid || !channel) {
    logger.debug('Missing appId, uid, or channel for RTM init');
    return null;
  }

  const existing = sessions.get(channel);
  if (existing) {
    return existing.readyPromise || existing.client;
  }

  const session = {
    client: null,
    appId,
    uid,
    token,
    channel,
    state: 'connecting',
    destroyed: false,
    reconnectAttempts: 0,
    reconnectTimer: null,
    readyPromise: null,
    initParams: { appId, uid, token, channel },
  };
  sessions.set(channel, session);
  session.readyPromise = connectSession(session);
  const client = await session.readyPromise;
  if (!client && !session.destroyed && sessions.get(channel) === session) {
    scheduleReconnection(channel);
  }
  return client;
}

async function connectSession(session) {
  const { appId, uid, token, channel } = session;
  try {
    session.state = 'connecting';
    const AgoraRTM = rtmModuleFactory();
    const rtmConfig = token ? { token } : {};
    const client = new AgoraRTM.RTM(appId, uid, rtmConfig);
    session.client = client;
    setupEventListeners(session, client);

    await client.login();
    logger.info(`[${channel}] Logged in as ${uid}`);

    await client.subscribe(channel, { withPresence: true });
    logger.info(`[${channel}] Subscribed (with presence)`);

    if (session.destroyed || sessions.get(channel) !== session) {
      await client.unsubscribe(channel).catch(() => {});
      await client.logout().catch(() => {});
      return null;
    }

    session.state = 'connected';
    return client;
  } catch (error) {
    session.state = 'failed';
    logger.error(`[${channel}] Failed to initialize RTM:`, error);
    return null;
  }
}

function setupEventListeners(session, client) {
  const { channel } = session;

  client.addEventListener('message', (event) => {
    try {
      for (const handler of messageHandlers) {
        try {
          handler(event);
        } catch (handlerError) {
          logger.error(`[${channel}] Error in message handler:`, handlerError);
        }
      }
    } catch (error) {
      logger.error(`[${channel}] Error processing RTM message:`, error);
    }
  });

  client.addEventListener('status', (event) => {
    logger.info(`[${channel}] Status: ${event.state}`);

    if (event.state === 'FAILED') {
      session.state = 'failed';
      scheduleReconnection(channel);
    } else if (event.state === 'CONNECTED') {
      session.state = 'connected';
      session.reconnectAttempts = 0;
    } else if (event.state === 'DISCONNECTED') {
      // The SDK handles transient disconnections. Reconnecting here caused an
      // idle-timeout loop that created hundreds of native RTM instances.
      session.state = 'disconnected';
    }
  });

  client.addEventListener('presence', (event) => {
    const type = event.eventType || event.type || 'unknown';
    const publisher = event.publisher || event.userId || 'unknown';
    const ch = event.channelName || channel;
    logger.info(`[${ch}] Presence: ${type} publisher=${publisher}`);

    // Log snapshot if available (initial user list on subscribe)
    if (event.snapshot) {
      const uids = Object.keys(event.snapshot);
      logger.info(`[${ch}] Presence snapshot: ${uids.length} user(s): ${uids.join(', ')}`);
    }

    // Fan out to registered presence handlers
    for (const handler of presenceHandlers) {
      try {
        handler(ch, event);
      } catch (e) {
        logger.error(`[${ch}] Presence handler error:`, e);
      }
    }
  });

  client.addEventListener('error', (error) => {
    logger.error(`[${channel}] RTM error: ${error.message || error}`, error);
  });
}

function scheduleReconnection(channel) {
  const session = sessions.get(channel);
  if (!session || session.destroyed || session.reconnectTimer) return;

  session.reconnectAttempts++;

  if (session.reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
    logger.error(`[${channel}] Max reconnection attempts (${MAX_RECONNECT_ATTEMPTS}) reached`);
    sessions.delete(channel);
    session.destroyed = true;
    return;
  }

  const delay = Math.min(
    BASE_RECONNECT_DELAY * Math.pow(2, session.reconnectAttempts - 1),
    MAX_RECONNECT_DELAY
  );

  logger.info(
    `[${channel}] Scheduling reconnection attempt ${session.reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS} in ${delay}ms`
  );

  session.reconnectTimer = setTimeout(async () => {
    session.reconnectTimer = null;
    if (session.destroyed || sessions.get(channel) !== session) return;
    try {
      try {
        if (session.client) await session.client.logout();
      } catch (e) {
        // ignore
      }
      session.client = null;
      session.readyPromise = connectSession(session);
      const result = await session.readyPromise;
      if (result) {
        logger.info(`[${channel}] Reconnected successfully`);
      } else {
        scheduleReconnection(channel);
      }
    } catch (error) {
      logger.error(`[${channel}] Reconnection failed:`, error);
      scheduleReconnection(channel);
    }
  }, delay);
}

/**
 * Send a message to an RTM channel.
 */
async function sendRTMMessage(channel, message) {
  const session = sessions.get(channel);
  if (!session) {
    logger.warn(`[${channel}] No RTM session — cannot send message`);
    return false;
  }

  try {
    await session.client.publish(channel, message);
    logger.debug(`[${channel}] Message sent`);
    return true;
  } catch (error) {
    logger.error(`[${channel}] Failed to send RTM message:`, error);
    return false;
  }
}

/**
 * Destroy the RTM session for a channel (called on unregister-agent).
 */
async function destroySession(channel) {
  const session = sessions.get(channel);
  if (!session) return;

  session.destroyed = true;
  if (session.reconnectTimer) {
    clearTimeout(session.reconnectTimer);
    session.reconnectTimer = null;
  }
  sessions.delete(channel);

  try {
    await session.client?.unsubscribe(channel).catch((e) => {
      logger.warn(`[${channel}] Unsubscribe error (ignored): ${e.message || e}`);
    });
    await session.client?.logout().catch((e) => {
      logger.warn(`[${channel}] Logout error (ignored): ${e.message || e}`);
    });
    logger.info(`[${channel}] Session destroyed`);
  } catch (error) {
    logger.error(`[${channel}] Error destroying session:`, error);
  }
}

/**
 * Check if RTM is connected for any channel (backwards-compatible).
 */
function isConnected() {
  return sessions.size > 0;
}

/**
 * Check if RTM is connected for a specific channel.
 */
function isChannelConnected(channel) {
  return sessions.has(channel);
}

/**
 * Register a handler for incoming RTM messages (from all sessions).
 */
function onRTMMessage(callback) {
  messageHandlers.push(callback);
}

/**
 * Register a handler for RTM presence events (join/leave/timeout).
 * Handler signature: (channel, event) => void
 * event.eventType: 'REMOTE_JOIN' | 'REMOTE_LEAVE' | 'REMOTE_TIMEOUT' | 'SNAPSHOT' etc.
 * event.publisher: the RTM UID that joined/left
 */
function onPresence(callback) {
  presenceHandlers.push(callback);
}

function setRTMModuleFactoryForTests(factory) {
  rtmModuleFactory = factory;
}

async function resetForTests() {
  await Promise.all([...sessions.keys()].map((channel) => destroySession(channel)));
  messageHandlers = [];
  presenceHandlers = [];
  rtmModuleFactory = () => require('rtm-nodejs');
}

function getSessionCountForTests() {
  return sessions.size;
}

module.exports = {
  initRTM,
  initRTMWithParams,
  sendRTMMessage,
  destroySession,
  onRTMMessage,
  onPresence,
  isConnected,
  isChannelConnected,
  setRTMModuleFactoryForTests,
  resetForTests,
  getSessionCountForTests,
};
