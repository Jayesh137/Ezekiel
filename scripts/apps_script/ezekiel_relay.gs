/**
 * Ezekiel relay — runs free on Google's servers (Google Apps Script), so the
 * project needs no always-on machine of its own.
 *
 * Every 5 minutes it does two things:
 *
 *  1. dispatchDue() keeps the GitHub workflows on cadence — watch every 10 min,
 *     collect 15, trace 30, scan 60, analyze daily — exactly as
 *     scripts/dispatch_workflows.ps1 does from a PC: never while that
 *     workflow's newest run is still queued or running, and never into the busy
 *     `data-commit` group (a group keeps one PENDING run, so queueing behind a
 *     busy group gets a run CANCELLED — the failure seen on 2026-09-17). GitHub's
 *     own crons were measured a median of 198 minutes apart here; this replaces
 *     them as the clock.
 *
 *  2. checkClusterActions() reads the Hyperliquid explorer for his config
 *     wallets and pushes to ntfy within minutes of a non-trading action that
 *     reaches OUTSIDE his cluster or hands out control: a withdrawal or send
 *     to an address not his, a new agent, a sub-account, a vault, a referrer.
 *     His own round trips (a Circle withdrawal to his own wallet) are skipped.
 *     The GitHub jobs report the same actions minutes later with full context;
 *     this is the early warning.
 *
 * If it cannot reach GitHub or the explorer it says so on ntfy, at most once
 * a day per problem: a tripwire that can die quietly is worth what a dead one
 * is worth.
 *
 * SETUP (about five minutes) — see scripts/apps_script/SETUP.md.
 */

var REPO = 'Jayesh137/Ezekiel';
var REF = 'main';
var RAW_CONFIG = 'https://raw.githubusercontent.com/' + REPO + '/' + REF + '/config.json';
var EXPLORER = 'https://rpc.hyperliquid.xyz/explorer';

var SCHEDULE = [
  { file: 'watch.yml', minutes: 10, group: 'watch' },
  { file: 'collect.yml', minutes: 15, group: 'data-commit' },
  { file: 'trace.yml', minutes: 30, group: 'data-commit' },
  { file: 'scan.yml', minutes: 60, group: 'data-commit' },
  { file: 'analyze.yml', minutes: 1440, group: 'data-commit' }
];
var DATA_COMMIT = ['collect.yml', 'trace.yml', 'scan.yml', 'analyze.yml',
                   'backfill.yml', 'substrate-backfill.yml'];

// Routine trading, never alerted (src/hl_actions.py TRADING_ACTIONS).
var TRADING = ['order', 'cancel', 'cancelByCloid', 'modify', 'batchModify',
               'updateLeverage', 'updateIsolatedMargin', 'twapOrder', 'twapCancel',
               'scheduleCancel', 'noop'];
// Where each action names the address it reaches (src/hl_actions.py DESTINATION_FIELDS).
var DESTINATION_FIELDS = {
  withdraw3: 'destination', sendToEvmWithData: 'destinationRecipient',
  usdSend: 'destination', spotSend: 'destination', sendAsset: 'destination',
  subAccountTransfer: 'subAccountUser', subAccountSpotTransfer: 'subAccountUser',
  vaultTransfer: 'vaultAddress', approveAgent: 'agentAddress', approveBuilderFee: 'builder',
  linkStakingUser: 'stakingUser'
};
// Not alerted: delegating stake to a validator moves nothing to a wallet, and
// the treasury does it routinely (6 of its 36 would-be alerts, measured).
var ROUTINE = ['tokenDelegate', 'cDeposit', 'cWithdraw'];
// Control handed out, alerted whatever address is involved.
var CONTROL = ['approveAgent', 'createSubAccount', 'convertToMultiSigUser', 'setReferrer',
               'vaultCreate', 'agentSetAbstraction', 'evmUserModify', 'linkStakingUser'];

// The explorer window is 300 actions. A script property holds ~9KB, so the
// seen-set stores an 18-character hash prefix: 300 of them fit (~6.3KB). The
// treasury alone showed 72 non-trading actions in its window on 2026-09-17,
// which a 60-entry cap would have dropped and re-alerted.
var SEEN_PER_WALLET = 300;
var HASH_PREFIX = 18;

function short_(hash) { return String(hash || '').toLowerCase().slice(0, HASH_PREFIX); }

// ---------------------------------------------------------------------------
// Pure decisions (exercised by scripts/apps_script/relay.test.mjs)
// ---------------------------------------------------------------------------

/**
 * @param schedule   SCHEDULE-shaped list
 * @param newest     { file: {status, created_at} | null }  null = unreadable
 * @param nowMs      current time
 * @param watchMinutes optional override for watch.yml (heightened mode)
 * @returns list of { file, dispatch: bool, reason }
 */
function decideDispatch(schedule, newest, nowMs, watchMinutes) {
  // An UNREADABLE run list (null) counts as busy: queueing behind a run we
  // cannot see is exactly how a pending run gets cancelled.
  var groupBusy = DATA_COMMIT.some(function (f) {
    var run = newest[f];
    return run === null || (run && run.status !== 'completed');
  });
  return schedule.map(function (job) {
    var run = newest[job.file];
    var minutes = (job.file === 'watch.yml' && watchMinutes) ? watchMinutes : job.minutes;
    if (run === null || run === undefined) {
      return { file: job.file, dispatch: false, reason: 'run list unreadable' };
    }
    if (run.status !== 'completed') {
      return { file: job.file, dispatch: false, reason: 'newest run is ' + run.status };
    }
    var ageMin = (nowMs - Date.parse(run.created_at)) / 60000;
    if (!(ageMin >= minutes)) {
      return { file: job.file, dispatch: false,
               reason: 'last run ' + ageMin.toFixed(1) + ' min ago (< ' + minutes + ')' };
    }
    if (job.group === 'data-commit' && groupBusy) {
      return { file: job.file, dispatch: false, reason: 'data-commit group busy' };
    }
    return { file: job.file, dispatch: true, reason: 'last run ' + ageMin.toFixed(1) + ' min ago' };
  });
}

/**
 * The other addresses a multi-sig action ties `wallet` to (src/hl_actions.py
 * multisig_parties): every signer named by convertToMultiSigUser (a JSON
 * string), or the account a multiSig action was signed for.
 */
function multisigParties(action, wallet) {
  var found = [];
  if (action.type === 'convertToMultiSigUser') {
    var signers = action.signers;
    if (typeof signers === 'string') {
      try { signers = JSON.parse(signers); } catch (e) { signers = null; }
    }
    found = (signers && Array.isArray(signers.authorizedUsers)) ? signers.authorizedUsers : [];
  } else if (action.type === 'multiSig') {
    var inner = action.payload || {};
    found = [inner.multiSigUser || action.multiSigUser, inner.outerSigner || action.outerSigner];
  }
  var out = [];
  found.forEach(function (value) {
    var a = String(value || '').toLowerCase();
    if (/^0x[0-9a-f]{40}$/.test(a) && a !== wallet && out.indexOf(a) < 0) out.push(a);
  });
  return out;
}

/**
 * Non-trading actions `wallet` performed that are not in `seen`, each marked
 * with whether it reaches outside the cluster or hands out control.
 */
function newActions(txs, wallet, seen, cluster, ignore) {
  var shared = ignore || [];
  var w = String(wallet || '').toLowerCase();
  var out = [];
  (txs || []).forEach(function (tx) {
    if (!tx || String(tx.user || '').toLowerCase() !== w || !tx.hash) return;
    var action = tx.action || {};
    var type = action.type;
    if (!type || TRADING.indexOf(type) >= 0 || ROUTINE.indexOf(type) >= 0 ||
        seen.indexOf(short_(tx.hash)) >= 0) return;
    var field = DESTINATION_FIELDS[type];
    var dest = field ? String(action[field] || '').toLowerCase() : '';
    var parties = multisigParties(action, w);
    if (!dest && parties.length) {
      // Name the stranger if there is one: a signer of his listed first must
      // not hide an outside signer listed second.
      var strangers = parties.filter(function (a) {
        return cluster.indexOf(a) < 0 && shared.indexOf(a) < 0;
      });
      dest = strangers.length ? strangers[0] : parties[0];
    }
    var outside = !!dest && /^0x[0-9a-f]{40}$/.test(dest) && cluster.indexOf(dest) < 0 &&
      shared.indexOf(dest) < 0;
    var control = CONTROL.indexOf(type) >= 0;
    out.push({
      parties: parties,
      hash: tx.hash, time: tx.time, type: type, destination: dest || null,
      amount: action.amount || action.usd || action.wei || null,
      chain: action.destinationChainId !== undefined ? action.destinationChainId : null,
      error: tx.error || null, alert: (outside || control) && !tx.error
    });
  });
  return out;
}

// ---------------------------------------------------------------------------
// I/O
// ---------------------------------------------------------------------------

function props_() { return PropertiesService.getScriptProperties(); }

function http_(url, options) {
  var opts = options || {};
  opts.muteHttpExceptions = true;
  var resp = UrlFetchApp.fetch(url, opts);
  return { code: resp.getResponseCode(), text: resp.getContentText() };
}

function ntfy_(title, message, priority, tags) {
  var topic = props_().getProperty('NTFY_TOPIC');
  if (!topic) { Logger.log('NTFY_TOPIC not set: ' + title); return false; }
  var r = http_('https://ntfy.sh/' + encodeURIComponent(topic), {
    method: 'post', payload: message,
    headers: { Title: title, Priority: priority || 'default', Tags: tags || 'rotating_light' }
  });
  return r.code >= 200 && r.code < 300;
}

/** Tell the operator once a day per problem, never silently die. */
function complainOnce_(key, title, message) {
  var p = props_();
  var last = Number(p.getProperty('COMPLAINED_' + key) || 0);
  if (Date.now() - last < 24 * 3600 * 1000) return;
  if (ntfy_(title, message, 'high', 'warning')) {
    p.setProperty('COMPLAINED_' + key, String(Date.now()));
  }
}

function github_(path, method, body) {
  var token = props_().getProperty('GITHUB_TOKEN');
  if (!token) return { code: 0, text: 'GITHUB_TOKEN not set' };
  return http_('https://api.github.com/repos/' + REPO + path, {
    method: method || 'get',
    contentType: 'application/json',
    payload: body ? JSON.stringify(body) : undefined,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28' }
  });
}

function newestRun_(file) {
  var r = github_('/actions/workflows/' + file + '/runs?per_page=1', 'get');
  if (r.code !== 200) return { error: r.code + ' ' + String(r.text).slice(0, 120) };
  var runs = (JSON.parse(r.text).workflow_runs || []);
  return runs.length ? { status: runs[0].status, created_at: runs[0].created_at } : null;
}

function dispatchDue() {
  if (!props_().getProperty('GITHUB_TOKEN')) return;   // tripwire-only install
  var files = DATA_COMMIT.concat(SCHEDULE.map(function (j) { return j.file; }));
  var newest = {}, failure = null;
  files.forEach(function (f) {
    if (newest.hasOwnProperty(f)) return;
    var run = newestRun_(f);
    if (run && run.error) { failure = f + ': ' + run.error; newest[f] = null; }
    else newest[f] = run || { status: 'completed', created_at: '1970-01-01T00:00:00Z' };
  });
  if (failure) {
    complainOnce_('github', 'Ezekiel relay cannot read GitHub',
                  'Workflows are not being dispatched. ' + failure +
                  '\nCheck the GITHUB_TOKEN script property (Actions: read and write).');
  }
  var heightenedUntil = Number(props_().getProperty('HEIGHTENED_UNTIL') || 0);
  var watchMinutes = Date.now() < heightenedUntil ? 5 : null;
  decideDispatch(SCHEDULE, newest, Date.now(), watchMinutes).forEach(function (d) {
    if (!d.dispatch) { Logger.log(d.file + ': ' + d.reason); return; }
    var r = github_('/actions/workflows/' + d.file + '/dispatches', 'post', { ref: REF });
    Logger.log(d.file + ': dispatch ' + (r.code === 204 ? 'ok' : 'FAILED ' + r.code) + ' (' + d.reason + ')');
    if (r.code !== 204) {
      complainOnce_('dispatch', 'Ezekiel relay dispatch failing',
                    d.file + ' dispatch returned ' + r.code + ': ' + String(r.text).slice(0, 160));
    }
  });
}

function clusterConfig_() {
  var r = http_(RAW_CONFIG, { method: 'get' });
  if (r.code !== 200) return null;
  var cfg = JSON.parse(r.text);
  var low = function (a) { return String(a).toLowerCase(); };
  return {
    wallets: [cfg.target_wallet].concat(cfg.known_self_wallets || []).filter(Boolean).map(low),
    // Venues everyone uses (HLP), exactly as the collector ignores them.
    shared: (cfg.hl_shared_destinations || []).concat(cfg.excluded_addresses || []).map(low)
  };
}

function checkClusterActions() {
  var conf = clusterConfig_();
  if (!conf) {
    complainOnce_('config', 'Ezekiel relay cannot read config.json',
                  'The fast tripwire is not running: ' + RAW_CONFIG);
    return;
  }
  var p = props_();
  var cluster = conf.wallets;
  cluster.forEach(function (wallet) {
    var r = http_(EXPLORER, { method: 'post', contentType: 'application/json',
                              payload: JSON.stringify({ type: 'userDetails', user: wallet }) });
    var txs = null;
    try { txs = r.code === 200 ? JSON.parse(r.text).txs : null; } catch (e) { txs = null; }
    if (!Array.isArray(txs)) {
      complainOnce_('explorer', 'Ezekiel relay cannot read the Hyperliquid explorer',
                    'userDetails for ' + wallet + ' returned ' + r.code + '. The fast tripwire is blind.');
      return;
    }
    var key = 'SEEN_' + wallet;
    var stored = p.getProperty(key);
    var seen = stored ? JSON.parse(stored) : null;
    var fresh = newActions(txs, wallet, seen || [], cluster, conf.shared);
    if (seen !== null) {
      fresh.filter(function (a) { return a.alert; }).forEach(function (a) {
        var title = 'Ezekiel EARLY WARNING: ' + a.type + ' by a wallet of his';
        var msg = 'Wallet: ' + wallet + '\nAction: ' + a.type +
          (a.destination ? '\nTo: ' + a.destination : '') +
          (a.parties.length > 1 ? '\nAll parties: ' + a.parties.join(', ') : '') +
          (a.amount ? '\nAmount: ' + a.amount : '') +
          (a.chain !== null ? '\nCircle domain: ' + a.chain : '') +
          '\nWhen: ' + new Date(Number(a.time)).toISOString() +
          '\nHyperliquid tx: ' + a.hash +
          '\n\nhttps://hypurrscan.io/address/' + (a.destination || wallet) +
          '\nThe GitHub jobs will follow up with full context.';
        ntfy_(title, msg, 'urgent', 'rotating_light');
        p.setProperty('HEIGHTENED_UNTIL', String(Date.now() + 6 * 3600 * 1000));
      });
    }
    // Remember NON-trading actions only. The explorer window is his last 300
    // actions, mostly orders; storing every hash under a 60-entry cap pushed a
    // real withdrawal out of memory while it was still in the window, so it
    // alerted again (found in a live dry run, 2026-09-17).
    var hashes = txs.filter(function (t) {
      return t && t.hash && String(t.user || '').toLowerCase() === wallet &&
        TRADING.indexOf((t.action || {}).type) < 0;
    }).map(function (t) { return short_(t.hash); });
    var merged = hashes.concat(seen || []).filter(function (h, i, arr) { return arr.indexOf(h) === i; });
    p.setProperty(key, JSON.stringify(merged.slice(0, SEEN_PER_WALLET)));
  });
}

/** The trigger target. */
function tick() {
  try { checkClusterActions(); } catch (e) { Logger.log('tripwire error: ' + e); }
  try { dispatchDue(); } catch (e) { Logger.log('dispatch error: ' + e); }
  props_().setProperty('LAST_TICK', new Date().toISOString());
}

/** Run once after pasting: installs the 5-minute trigger and seeds the tripwire. */
function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'tick') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('tick').timeBased().everyMinutes(5).create();
  tick();
  ntfy_('Ezekiel relay installed', 'Dispatching GitHub workflows and watching his wallets every 5 minutes.',
        'default', 'white_check_mark');
}
