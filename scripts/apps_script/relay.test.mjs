// scripts/apps_script/relay.test.mjs — run: node --test scripts/apps_script/
// Loads the Apps Script file into a sandbox with Google's globals mocked, so the
// relay that replaces an always-on machine is tested like everything else.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SOURCE = readFileSync(new URL('./ezekiel_relay.gs', import.meta.url), 'utf8');
const T = '0x45d26f28196d226497130c4bac709d808fed4029';
const TREASURY = '0x1419e75330c71ce463102e6a1eb62fe80b412d5f';
const OUTSIDE = '0x' + 'ab'.repeat(20);

function load({ responses = {}, props = {} } = {}) {
  const store = { ...props };
  const calls = [];
  const sandbox = {
    Logger: { log: () => {} },
    PropertiesService: { getScriptProperties: () => ({
      getProperty: (k) => (k in store ? store[k] : null),
      setProperty: (k, v) => { store[k] = String(v); }
    }) },
    UrlFetchApp: { fetch: (url, opts) => {
      calls.push({ url, opts });
      const handler = Object.entries(responses).find(([k]) => url.includes(k));
      const [code, body] = handler ? handler[1](url, opts) : [404, ''];
      return { getResponseCode: () => code, getContentText: () => body };
    } },
    ScriptApp: { getProjectTriggers: () => [], deleteTrigger: () => {},
                 newTrigger: () => ({ timeBased: () => ({ everyMinutes: () => ({ create: () => {} }) }) }) },
    Date, JSON, Number, String, encodeURIComponent
  };
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);
  return { sandbox, store, calls };
}

const NOW = Date.parse('2026-09-17T04:00:00Z');
const ago = (min) => new Date(NOW - min * 60000).toISOString();

test('dispatch waits while a run is queued and when the interval has not passed', () => {
  const { sandbox } = load();
  const newest = {
    'watch.yml': { status: 'queued', created_at: ago(30) },
    'collect.yml': { status: 'completed', created_at: ago(5) },
    'trace.yml': { status: 'completed', created_at: ago(45) },
    'scan.yml': { status: 'completed', created_at: ago(90) },
    'analyze.yml': { status: 'completed', created_at: ago(2000) },
    'backfill.yml': { status: 'completed', created_at: ago(9999) },
    'substrate-backfill.yml': { status: 'completed', created_at: ago(9999) }
  };
  const d = Object.fromEntries(sandbox.decideDispatch(sandbox.SCHEDULE, newest, NOW).map((x) => [x.file, x]));
  assert.equal(d['watch.yml'].dispatch, false);
  assert.equal(d['collect.yml'].dispatch, false);
  assert.equal(d['trace.yml'].dispatch, true);
  assert.equal(d['scan.yml'].dispatch, true);
  assert.equal(d['analyze.yml'].dispatch, true);
});

test('nothing is queued into a busy data-commit group (that is what cancels runs)', () => {
  const { sandbox } = load();
  const newest = {
    'watch.yml': { status: 'completed', created_at: ago(11) },
    'collect.yml': { status: 'completed', created_at: ago(20) },
    'trace.yml': { status: 'in_progress', created_at: ago(3) },
    'scan.yml': { status: 'completed', created_at: ago(90) },
    'analyze.yml': { status: 'completed', created_at: ago(2000) },
    'backfill.yml': { status: 'completed', created_at: ago(9999) },
    'substrate-backfill.yml': { status: 'completed', created_at: ago(9999) }
  };
  const d = Object.fromEntries(sandbox.decideDispatch(sandbox.SCHEDULE, newest, NOW).map((x) => [x.file, x]));
  assert.equal(d['watch.yml'].dispatch, true, 'the watch has its own group');
  for (const f of ['collect.yml', 'scan.yml', 'analyze.yml']) {
    assert.equal(d[f].dispatch, false, f);
  }
});

test('an unreadable run list never dispatches, and heightened mode tightens the watch', () => {
  const { sandbox } = load();
  const newest = { 'watch.yml': { status: 'completed', created_at: ago(6) } };
  const plain = sandbox.decideDispatch([{ file: 'watch.yml', minutes: 10, group: 'watch' }], newest, NOW);
  const heightened = sandbox.decideDispatch([{ file: 'watch.yml', minutes: 10, group: 'watch' }], newest, NOW, 5);
  assert.equal(plain[0].dispatch, false);
  assert.equal(heightened[0].dispatch, true);
  const unreadable = sandbox.decideDispatch([{ file: 'scan.yml', minutes: 60, group: 'data-commit' }], { 'scan.yml': null }, NOW);
  assert.equal(unreadable[0].dispatch, false);
});

test('new actions: trading and his own round trips are quiet; outside sends and control alert', () => {
  const { sandbox } = load();
  const cluster = [T, TREASURY];
  const txs = [
    { hash: '0x1', user: T, time: NOW, action: { type: 'order' } },
    { hash: '0x2', user: T, time: NOW, action: { type: 'sendToEvmWithData', destinationRecipient: T, amount: '7000000', destinationChainId: 3 } },
    { hash: '0x3', user: T, time: NOW, action: { type: 'usdSend', destination: OUTSIDE, amount: '10' } },
    { hash: '0x4', user: T, time: NOW, action: { type: 'approveAgent', agentAddress: '0x' + '98'.repeat(20) } },
    { hash: '0x5', user: T, time: NOW, action: { type: 'withdraw3', destination: OUTSIDE }, error: 'insufficient' },
    { hash: '0x6', user: OUTSIDE, time: NOW, action: { type: 'usdSend', destination: T } },
    { hash: '0x7', user: T, time: NOW, action: { type: 'usdSend', destination: OUTSIDE } }
  ];
  const got = sandbox.newActions(txs, T, [sandbox.short_('0x7')], cluster);
  const byHash = Object.fromEntries(got.map((a) => [a.hash, a]));
  assert.deepEqual(Object.keys(byHash).sort(), ['0x2', '0x3', '0x4', '0x5']);
  assert.equal(byHash['0x2'].alert, false, 'a Circle withdrawal to himself is his routine loop');
  assert.equal(byHash['0x3'].alert, true, 'even $10 to an outside address');
  assert.equal(byHash['0x4'].alert, true, 'a new agent is control handed out');
  assert.equal(byHash['0x5'].alert, false, 'a failed action moved nothing');
});

function explorer(txsByWallet) {
  return (url, opts) => {
    const user = JSON.parse(opts.payload).user;
    return [200, JSON.stringify({ type: 'userDetails', txs: txsByWallet[user] || [] })];
  };
}

test('the first tick seeds silently; a later outside send pushes an urgent ntfy once', () => {
  const config = JSON.stringify({ target_wallet: T, known_self_wallets: [TREASURY] });
  const txs = { [T]: [{ hash: '0xold', user: T, time: NOW, action: { type: 'usdSend', destination: OUTSIDE } }] };
  const { sandbox, store, calls } = load({
    props: { NTFY_TOPIC: 'topic' },
    responses: {
      'config.json': () => [200, config],
      'explorer': explorer(txs),
      'ntfy.sh': () => [200, 'ok']
    }
  });
  sandbox.checkClusterActions();
  assert.equal(calls.filter((c) => c.url.includes('ntfy.sh')).length, 0, 'history was announced');
  assert.ok(store['SEEN_' + T].includes(sandbox.short_('0xold')));

  txs[T].unshift({ hash: '0xnew', user: T, time: NOW, action: { type: 'withdraw3', destination: OUTSIDE, amount: '5000000' } });
  sandbox.checkClusterActions();
  const pushes = calls.filter((c) => c.url.includes('ntfy.sh'));
  assert.equal(pushes.length, 1);
  assert.equal(pushes[0].opts.headers.Priority, 'urgent');
  assert.match(pushes[0].opts.payload, /withdraw3|5000000/);
  assert.ok(Number(store.HEIGHTENED_UNTIL) > Date.now(), 'the watch cadence tightens after a move');

  sandbox.checkClusterActions();
  assert.equal(calls.filter((c) => c.url.includes('ntfy.sh')).length, 1, 'the same action alerted twice');
});

test('a blind explorer or GitHub complains on ntfy, at most once a day', () => {
  const config = JSON.stringify({ target_wallet: T, known_self_wallets: [] });
  const { sandbox, calls } = load({
    props: { NTFY_TOPIC: 'topic', GITHUB_TOKEN: 'x' },
    responses: {
      'config.json': () => [200, config],
      'explorer': () => [429, 'rate limited'],
      'api.github.com': () => [401, 'Bad credentials'],
      'ntfy.sh': () => [200, 'ok']
    }
  });
  sandbox.tick();
  sandbox.tick();
  const titles = calls.filter((c) => c.url.includes('ntfy.sh')).map((c) => c.opts.headers.Title);
  assert.deepEqual(titles.sort(), ['Ezekiel relay cannot read GitHub',
                                   'Ezekiel relay cannot read the Hyperliquid explorer']);
  assert.equal(calls.filter((c) => c.url.includes('/dispatches')).length, 0, 'dispatched on unreadable state');
});

test('without a GitHub token it runs as a tripwire only', () => {
  const { sandbox, calls } = load({ props: { NTFY_TOPIC: 't' }, responses: { 'config.json': () => [500, ''] } });
  sandbox.dispatchDue();
  assert.equal(calls.filter((c) => c.url.includes('api.github.com')).length, 0);
});

test('an unreadable data-commit run list keeps the whole group waiting', () => {
  const { sandbox } = load();
  const newest = {
    'collect.yml': { status: 'completed', created_at: ago(20) },
    'backfill.yml': null
  };
  const d = sandbox.decideDispatch([{ file: 'collect.yml', minutes: 15, group: 'data-commit' }], newest, NOW);
  assert.equal(d[0].dispatch, false);
  assert.match(d[0].reason, /busy/);
});

test('a withdrawal buried under hundreds of newer orders never alerts twice', () => {
  const config = JSON.stringify({ target_wallet: T, known_self_wallets: [] });
  const withdrawal = { hash: '0xwd', user: T, time: NOW, action: { type: 'withdraw3', destination: OUTSIDE } };
  const orders = (n) => Array.from({ length: n }, (_, i) => ({ hash: '0xo' + i + '_' + Math.random(), user: T, time: NOW, action: { type: 'order' } }));
  const txs = { [T]: [] };
  const { sandbox, calls } = load({
    props: { NTFY_TOPIC: 'topic' },
    responses: { 'config.json': () => [200, config], 'explorer': explorer(txs), 'ntfy.sh': () => [200, 'ok'] }
  });
  sandbox.checkClusterActions();                         // seed: nothing yet
  txs[T] = [withdrawal, ...orders(10)];
  sandbox.checkClusterActions();                         // alert once
  txs[T] = [...orders(250), withdrawal];                 // 250 newer orders, still in the window
  sandbox.checkClusterActions();
  sandbox.checkClusterActions();
  assert.equal(calls.filter((c) => c.url.includes('ntfy.sh')).length, 1);
});

test('the seen-set holds a full 300-action window inside a script property', () => {
  const { sandbox } = load();
  const hashes = Array.from({ length: 300 }, (_, i) => '0x' + String(i).padStart(64, 'f'));
  const stored = JSON.stringify(hashes.map((h) => sandbox.short_(h)));
  assert.ok(stored.length < 9000, `${stored.length} bytes exceeds the 9KB property limit`);
});

test('staking and shared venues like HLP are routine, not early warnings', () => {
  const { sandbox } = load();
  const HLP = '0xdfc24b077bc1425ad1dea75bcb6f8158e10df303';
  const txs = [
    { hash: '0xa', user: T, time: NOW, action: { type: 'tokenDelegate', validator: OUTSIDE } },
    { hash: '0xb', user: T, time: NOW, action: { type: 'vaultTransfer', vaultAddress: HLP, isDeposit: true } },
    { hash: '0xc', user: T, time: NOW, action: { type: 'vaultTransfer', vaultAddress: OUTSIDE, isDeposit: true } }
  ];
  const got = sandbox.newActions(txs, T, [], [T], [HLP]);
  assert.deepEqual([...got.filter((a) => a.alert).map((a) => a.hash)], ['0xc']);
  assert.equal(got.find((a) => a.hash === '0xa'), undefined, 'staking delegation was treated as news');
});

test('multi-sig names the outside signer or co-signed account, never hiding it behind his own', () => {
  const { sandbox } = load();
  const SIGNER = '0x' + '5a'.repeat(20);
  const txs = [
    { hash: '0xm1', user: T, time: NOW, action: { type: 'convertToMultiSigUser',
      signers: JSON.stringify({ authorizedUsers: [TREASURY, SIGNER.toUpperCase().replace('0X', '0x')], threshold: 1 }) } },
    { hash: '0xm2', user: T, time: NOW, action: { type: 'multiSig', payload: { multiSigUser: OUTSIDE, outerSigner: T } } },
    { hash: '0xm3', user: T, time: NOW, action: { type: 'multiSig', payload: { multiSigUser: TREASURY, outerSigner: T } } }
  ];
  const got = [...sandbox.newActions(txs, T, [], [T, TREASURY])];
  assert.deepEqual(got.map((a) => [a.hash, a.destination, a.alert]), [
    ['0xm1', SIGNER, true],
    ['0xm2', OUTSIDE, true],
    ['0xm3', TREASURY, false]
  ]);
  assert.deepEqual([...got[0].parties], [TREASURY, SIGNER]);
});
