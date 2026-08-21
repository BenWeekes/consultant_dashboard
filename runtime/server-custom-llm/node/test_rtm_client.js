const test = require('node:test');
const assert = require('node:assert/strict');

const rtm = require('./rtm_client');

class FakeRTM {
  static instances = [];

  constructor(appId, uid) {
    this.appId = appId;
    this.uid = uid;
    this.listeners = new Map();
    this.logoutCalls = 0;
    FakeRTM.instances.push(this);
  }

  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) || [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }

  emit(name, event) {
    for (const handler of this.listeners.get(name) || []) handler(event);
  }

  async login() {
    await new Promise((resolve) => setTimeout(resolve, 20));
    this.emit('status', { state: 'CONNECTED' });
  }

  async subscribe() {}
  async unsubscribe() {}
  async publish() {}

  async logout() {
    this.logoutCalls += 1;
  }
}

test.beforeEach(async () => {
  FakeRTM.instances = [];
  await rtm.resetForTests();
  rtm.setRTMModuleFactoryForTests(() => ({ RTM: FakeRTM }));
});

test.afterEach(async () => {
  await rtm.resetForTests();
});

test('concurrent initialization shares one RTM client per channel', async () => {
  const first = rtm.initRTMWithParams('app', 'uid', 'token', 'channel');
  const second = rtm.initRTMWithParams('app', 'uid', 'token', 'channel');
  const [firstClient, secondClient] = await Promise.all([first, second]);

  assert.equal(FakeRTM.instances.length, 1);
  assert.equal(firstClient, secondClient);
  assert.equal(rtm.getSessionCountForTests(), 1);
});

test('a disconnected status does not create a replacement client', async () => {
  await rtm.initRTMWithParams('app', 'uid', 'token', 'channel');
  FakeRTM.instances[0].emit('status', { state: 'DISCONNECTED' });
  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.equal(FakeRTM.instances.length, 1);
  assert.equal(rtm.getSessionCountForTests(), 1);
});

test('destroy removes the session and logs out its client', async () => {
  await rtm.initRTMWithParams('app', 'uid', 'token', 'channel');
  const client = FakeRTM.instances[0];
  await rtm.destroySession('channel');

  assert.equal(rtm.getSessionCountForTests(), 0);
  assert.equal(client.logoutCalls, 1);
});
