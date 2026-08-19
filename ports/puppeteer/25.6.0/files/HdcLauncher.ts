/**
 * HdcLauncher — HarmonyOS browser launcher via hdc
 *
 * launchViaHdc(): aa force-stop → aa start → waitForCdpEndpoint → CdpBrowser
 * closeCallback: Browser.close CDP → disconnected event → force-stop fallback
 *
 * This file is added to puppeteer-core source during OHOS build.
 * BrowserLauncher.ts patch imports launchViaHdc from this file.
 */

import {execFileSync} from 'child_process';
import * as path from 'path';
import * as http from 'http';

// ─── Constants ───

const CHANNEL_MAP: Record<string, string> = {
  chrome: 'com.haitai.htbrowser',
  'chrome-beta': 'com.huawei.ohos_chromium',
};

const ABILITY_MAP: Record<string, string> = {
  'com.huawei.ohos_chromium': 'BrowserAbility',
  'com.haitai.htbrowser': 'EntryAbility',
};

const BOOL_FLAGS = [
  'disable-extensions', 'disable-default-apps', 'disable-component-extensions-with-background-pages',
  'no-first-run', 'no-default-browser-check', 'disable-background-networking',
  'disable-client-side-phishing-detection', 'disable-popup-blocking', 'disable-prompt-on-repost',
  'disable-breakpad', 'disable-hang-monitor', 'disable-ipc-flooding-protection',
  'metrics-recording-only', 'disable-sync', 'disable-search-engine-choice-screen',
  'export-tagged-pdf', 'disable-background-timer-throttling', 'disable-renderer-backgrounding',
  'disable-backgrounding-occluded-windows', 'disable-field-trial-config', 'disable-component-update',
  'disable-dev-shm-usage', 'no-service-autorun', 'disable-infobars', 'allow-file-access-from-files',
  'enable-automation', 'allow-pre-commit-input', 'disable-edgeupdater',
  'edge-skip-compat-layer-relaunch', 'unsafely-disable-devtools-self-xss-warnings',
];

const DISABLED_FEATURES = [
  'AvoidUnnecessaryBeforeUnloadCheckSync', 'BoundaryEventDispatchTracksNodeRemoval',
  'DestroyProfileOnBrowserClose', 'DialMediaRouteProvider', 'GlobalMediaControls',
  'HttpsUpgrades', 'LensOverlay', 'MediaRouter', 'PaintHolding',
  'ThirdPartyStoragePartitioning', 'Translate', 'AutoDeElevate', 'RenderDocument', 'OptimizationHints',
].join(',');

function resolveBrowserPackage(options: Record<string, any>): string {
  if (options.harmonyBundleName) return options.harmonyBundleName;
  if (options.channel) return CHANNEL_MAP[options.channel] || options.channel;
  const env = process.env.HARMONY_BROWSER;
  if (env) return ({chrome: 'com.huawei.ohos_chromium', haitai: 'com.haitai.htbrowser'} as any)[env] || env;
  return 'com.haitai.htbrowser';
}

function resolveAbility(pkg: string): string {
  return ABILITY_MAP[pkg] || 'EntryAbility';
}

function buildLaunchParams(options: Record<string, any>): Record<string, any> {
  const port = options.cdpPort || options.debuggingPort || 9333;
  const args = [`--remote-debugging-port=${port}`, `--remote-allow-origins=http://127.0.0.1:${port}`];
  for (const f of BOOL_FLAGS) args.push(`--${f}`);
  args.push('--password-store=basic', '--use-mock-keychain', '--force-color-profile=srgb');
  args.push(`--disable-features=${DISABLED_FEATURES}`, '--enable-features=CDPScreenshotNewSurface');
  if (options.userDataDir) args.push(`--user-data-dir=${options.userDataDir}`);
  return {stringParams: {cmdArgs: args.join(' ')}};
}

// ─── hdc ───

function hdcPath(): string {
  try { require('fs').accessSync('/data/service/hnp/bin/hdc'); return '/data/service/hnp/bin/hdc'; } catch {}
  return process.env.HDC_PATH || 'hdc';
}

function execHdc(args: string[], timeout = 30000): string {
  return execFileSync(hdcPath(), args, {timeout, encoding: 'utf-8', stdio: ['pipe', 'pipe', 'pipe']}).trim();
}

async function ensureDeviceConnected(): Promise<void> {
  const targets = execHdc(['list', 'targets']).split('\n').map(s => s.trim()).filter(Boolean);
  if (targets.length > 0 && targets[0] !== '[Empty]') return;
  try {
    const port = parseInt(execFileSync('param', ['get', 'persist.hdc.port'], {timeout: 3000, encoding: 'utf-8', stdio: ['ignore', 'pipe', 'pipe']}).trim(), 10);
    if (port > 0 && port <= 65535 && execHdc(['tconn', `127.0.0.1:${port}`], 10000).includes('Connect OK')) return;
  } catch {}
  throw new Error('No HarmonyOS device connected.');
}

// ─── Process management ───

async function waitForProcessDeath(pkg: string, timeoutMs: number): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const out = execHdc(['shell', `ps -ef | grep ${pkg}`], 5000);
      const alive = out.split('\n').some(line => line.includes(pkg) && !line.includes('grep'));
      if (!alive) return true;
    } catch {}
    await new Promise(r => setTimeout(r, 500));
  }
  return false;
}

async function getBrowserPid(pkg: string, timeoutMs: number): Promise<number> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const out = execHdc(['shell', `ps -ef | grep ${pkg}`], 5000);
      for (const line of out.split('\n')) {
        if (line.includes('grep')) continue;
        const parts = line.trim().split(/\s+/);
        if (parts.length >= 8 && parts[parts.length - 1] === pkg) {
          const pid = parseInt(parts[1], 10);
          if (pid > 0) return pid;
        }
      }
    } catch {}
    await new Promise(r => setTimeout(r, 500));
  }
  return 0;
}

// ─── CDP ───

function tryCdpEndpoint(url: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res: any) => {
      let d = ''; res.on('data', (c: any) => d += c);
      res.on('end', () => {
        if (res.statusCode !== 200) return reject(new Error(`HTTP ${res.statusCode}`));
        try { const j = JSON.parse(d); j.webSocketDebuggerUrl ? resolve(j.webSocketDebuggerUrl) : reject(new Error('No ws')); }
        catch { reject(new Error('Parse failed')); }
      });
    });
    req.on('error', reject);
    req.setTimeout(2000, () => { req.destroy(); reject(new Error('Timeout')); });
  });
}

async function waitForCdpEndpoint(port: number, timeoutMs: number): Promise<string> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try { return await tryCdpEndpoint(`http://127.0.0.1:${port}/json/version`); }
    catch { try { return await tryCdpEndpoint(`http://[::1]:${port}/json/version`); } catch { await new Promise(r => setTimeout(r, 500)); } }
  }
  throw new Error(`CDP endpoint timeout on port ${port}`);
}

async function waitForBrowserReady(wsEndpoint: string, timeoutMs: number): Promise<void> {
  const {NodeWebSocketTransport} = await import('../node/NodeWebSocketTransport.js');
  const {Connection} = await import('../cdp/Connection.js');
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const t = await NodeWebSocketTransport.create(wsEndpoint);
      const conn = new Connection(wsEndpoint, t, 0, undefined, false, undefined, undefined);
      const result = await conn.send('Target.createTarget', {url: 'about:blank'});
      if (result.targetId) return;
    } catch {}
    await new Promise(r => setTimeout(r, 1000));
  }
}

// ─── Runtime injection ───

async function injectScripts(browser: any): Promise<void> {
  const pages = await browser.defaultBrowserContext().pages().catch(() => []);
  if (!pages.length) return;
  const getClient = (p: any) => typeof p._client === 'function' ? p._client() : null;
  const inject = async (src: string) => {
    for (const p of pages) { const c = getClient(p); if (c) { try { await c.send('Page.addScriptToEvaluateOnNewDocument', {source: src}); } catch {} } }
  };
  try { await inject('Object.defineProperty(navigator,"webdriver",{get:()=>true,configurable:true})'); } catch {}
  try { await inject("if(typeof trustedTypes!=='undefined'){try{trustedTypes.createPolicy('default',{createHTML:s=>s,createScript:s=>s,createScriptURL:s=>s})}catch(e){}}"); } catch {}
}

// ─── Module loader ───

async function loadModule(modulePath: string): Promise<any> {
  const root = path.dirname(require.resolve('puppeteer-core/package.json'));
  return await import(path.join(root, 'lib', 'esm', 'puppeteer', modulePath));
}

// ─── launchViaHdc ───

export async function launchViaHdc(launcher: any, options: Record<string, any> = {}): Promise<any> {
  const pkg = resolveBrowserPackage(options);
  const cdpPort = options.cdpPort || options.debuggingPort || 9333;

  if (options.hdcPath) process.env.HDC_PATH = options.hdcPath;

  // 1. Connect device
  await ensureDeviceConnected();

  // 2. Check browser installed
  if (!execHdc(['shell', 'bm', 'dump', '-a']).includes(pkg)) {
    throw new Error(`Browser "${pkg}" is not installed.`);
  }

  // 3. Kill existing browser (same as child_process.spawn replaces existing)
  execHdc(['shell', 'aa', 'force-stop', pkg]);
  await waitForProcessDeath(pkg, 10000);

  // 4. Start fresh browser (same as child_process.spawn)
  const ability = resolveAbility(pkg);
  const wantParams = buildLaunchParams(options);
  const args = ['shell', 'aa', 'start', '-b', pkg, '-a', ability];
  if (wantParams.stringParams) for (const [k, v] of Object.entries(wantParams.stringParams)) args.push('--ps', k, String(v));
  execHdc(args);

  // 5. Get PID
  const browserPid = await getBrowserPid(pkg, 10000);

  // 6. Wait for CDP endpoint
  const wsEndpoint = await waitForCdpEndpoint(cdpPort, 30000);

  // 7. Wait for browser ready (Target.createTarget works)
  await waitForBrowserReady(wsEndpoint, 20000);

  // 8. Create main connection
  const {NodeWebSocketTransport} = await loadModule('node/NodeWebSocketTransport.js');
  const {Connection} = await loadModule('cdp/Connection.js');
  const {CdpBrowser} = await loadModule('cdp/Browser.js');
  const {CDPSessionEvent} = await loadModule('api/CDPSession.js');

  const transport = await NodeWebSocketTransport.create(wsEndpoint, undefined, options.logger);
  const connection = new Connection(wsEndpoint, transport, options.slowMo || 0, options.protocolTimeout, false, undefined, options.logger);

  // 9. closeCallback (matches other platforms' closeBrowser)
  let connectionClosed = false;
  connection.once(CDPSessionEvent.Disconnected, () => { connectionClosed = true; });

  const closeCallback = async () => {
    if (connection && !connectionClosed) {
      try {
        const closed = new Promise<void>((resolve) => {
          connection.once(CDPSessionEvent.Disconnected, () => resolve());
        });
        await connection.closeBrowser();
        await Promise.race([
          closed,
          new Promise((_, reject) => setTimeout(() => reject(new Error('Browser did not close within 5s')), 5000)),
        ]);
      } catch {
        execHdc(['shell', 'aa', 'force-stop', pkg]);
        await waitForProcessDeath(pkg, 10000);
      }
    } else {
      const exited = await waitForProcessDeath(pkg, 5000);
      if (!exited) {
        execHdc(['shell', 'aa', 'force-stop', pkg]);
        await waitForProcessDeath(pkg, 10000);
      }
    }
  };

  // 10. mock process
  const mockProcess = {
    pid: browserPid,
    kill: () => { try { execHdc(['shell', 'aa', 'force-stop', pkg]); } catch {} },
    on: () => {},
  };

  // 11. defaultViewport
  const defaultViewport = options.defaultViewport !== undefined
    ? options.defaultViewport
    : {width: 800, height: 600};

  // 12. Create CdpBrowser
  const browser = await CdpBrowser._create(
    connection, [],
    options.acceptInsecureCerts || false,
    defaultViewport,
    options.downloadBehavior, mockProcess, closeCallback,
    options.targetFilter, undefined, true,
    options.networkEnabled ?? true, options.issuesEnabled ?? true,
    options.handleDevToolsAsPage || false, options.blocklist, options.allowlist, options.logger,
  );

  browser._isCollocatedWithServer = false;

  // 13. Inject scripts
  await injectScripts(browser);

  // 14. Clean up about:blank pages
  const initialPages = await browser.defaultBrowserContext().pages().catch(() => []);
  for (const p of initialPages) {
    const pUrl = p.url();
    if (pUrl === 'about:blank' || pUrl === '') await p.close().catch(() => {});
  }

  return browser;
}
