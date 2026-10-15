#!/usr/bin/env node
/**
 * Fetch the latest Meta Quest build of one app and lay it out for FramePort.
 *
 * FramePort never downloads apps, so the APK has to come from somewhere. This
 * wraps AXRB's Meta store client (tools/vendor/axrb/meta.mjs) to do the one
 * thing it is scoped to: pull the newest Android build of an app you are
 * entitled to, straight from Meta, with no headset and no Windows needed.
 *
 *   node tools/quest_store_fetch.mjs --login
 *   node tools/quest_store_fetch.mjs --list
 *   node tools/quest_store_fetch.mjs --app 2497738113633933 [--dry-run]
 *
 * Requires Node 18+ (global fetch). GPL-3.0; see tools/vendor/axrb/README.md.
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import readline from 'node:readline/promises';
import { fileURLToPath } from 'node:url';
import { MetaAuth, QuestStore, appId } from './vendor/axrb/meta.mjs';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const CONFIG_DIR = path.join(os.homedir(), '.config', 'steam_frame');
const TOKEN_FILE = path.join(CONFIG_DIR, 'meta-token.json');
const PENDING_FILE = path.join(CONFIG_DIR, 'pending-login.json');
const GAMES_DIR = path.join(ROOT, 'Games');

const c = {
  dim: (s) => `\x1b[2m${s}\x1b[0m`,
  bold: (s) => `\x1b[1m${s}\x1b[0m`,
  green: (s) => `\x1b[32m${s}\x1b[0m`,
  yellow: (s) => `\x1b[33m${s}\x1b[0m`,
  red: (s) => `\x1b[31m${s}\x1b[0m`,
  cyan: (s) => `\x1b[36m${s}\x1b[0m`,
};

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--login') out.login = true;
    else if (a === '--list') out.list = true;
    else if (a === '--dry-run') out.dryRun = true;
    else if (a === '--callback') out.callback = argv[++i];
    else if (a === '--app') out.app = argv[++i];
    else if (a === '--out') out.out = argv[++i];
    else if (a === '--yes') out.yes = true;
    else if (a === '-h' || a === '--help') out.help = true;
    else if (a.startsWith('-')) throw new Error(`unknown option ${a}`);
    else out._.push(a);
  }
  return out;
}

const HELP = `
Fetch a Quest app from the Meta store for FramePort.

  --login                 sign in to Meta, save the session token
  --callback <oculus://>  finish a sign-in started with --login
  --list                  show your entitled Quest library
  --app <id|url>          app to fetch (id, or a meta.com store URL)
  --build <id>            pick a specific build instead of the newest
  --dry-run               show what would be downloaded, download nothing
  --out <dir>             output root (default ./Games)
  --yes                   do not ask for confirmation
`;

// ---------------------------------------------------------------- session

function loadToken() {
  try {
    return JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8')).token ?? null;
  } catch {
    return null;
  }
}

function saveToken(token) {
  writeSecret(TOKEN_FILE, { token, saved: new Date().toISOString() });
}

/**
 * Meta's sign-in challenge expires after 10 minutes, and a first-time sign-in
 * takes longer than that. Keep it on disk so --login and --callback can happen
 * in two separate runs.
 */
function savePending(challenge, expires) {
  writeSecret(PENDING_FILE, { challenge, expires });
}

function loadPending() {
  try {
    return JSON.parse(fs.readFileSync(PENDING_FILE, 'utf8'));
  } catch {
    return null;
  }
}

function writeSecret(file, value) {
  fs.mkdirSync(CONFIG_DIR, { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n', { mode: 0o600 });
  fs.chmodSync(file, 0o600);
}

async function doLogin(args) {
  if (!args.callback) {
    const auth = new MetaAuth();
    const url = await auth.begin();
    savePending(auth.challenge, auth.expires);
    console.log(c.bold('Sign in to Meta'));
    console.log(c.dim('  1. Open this URL in a browser and sign in to your Meta account.'));
    console.log(`\n  ${c.cyan(url)}\n`);
    console.log(
      c.dim('  2. The browser cannot open oculus://, so it will show an error.\n' +
        '     That is expected: copy the FULL url from the address bar.\n')
    );
    console.log(c.dim('  3. Then run:'));
    console.log(c.dim('       node tools/quest_store_fetch.mjs --callback "<that url>"'));
    console.log(
      c.dim('\n  The link is valid for 10 minutes. Re-run --login for a fresh one.')
    );
    return 1;
  }

  const pending = loadPending();
  if (!pending) {
    console.error(c.red('No sign-in in progress.'));
    console.error(c.dim('  Run:  node tools/quest_store_fetch.mjs --login'));
    process.exit(1);
  }
  if (Date.now() > pending.expires) {
    fs.rmSync(PENDING_FILE, { force: true });
    console.error(c.red('That sign-in link expired (they last 10 minutes).'));
    console.error(c.dim('  Run:  node tools/quest_store_fetch.mjs --login'));
    process.exit(1);
  }

  const auth = new MetaAuth();
  auth.challenge = pending.challenge;
  auth.expires = pending.expires;

  const token = await auth.complete(args.callback.trim());
  saveToken(token);
  fs.rmSync(PENDING_FILE, { force: true });
  console.log(c.green('Signed in.'));
  console.log(c.dim(`  token saved to ${TOKEN_FILE} (mode 600)`));
  return 0;
}

function requireToken() {
  const token = loadToken();
  if (!token) {
    console.error(c.red('Not signed in.'));
    console.error(c.dim('  Run:  node tools/quest_store_fetch.mjs --login'));
    process.exit(1);
  }
  return token;
}

// ------------------------------------------------------------------ fetch

function human(n) {
  if (!Number.isFinite(n) || n <= 0) return '?';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function sha256(file) {
  return new Promise((resolve, reject) => {
    const h = crypto.createHash('sha256');
    fs.createReadStream(file)
      .on('data', (d) => h.update(d))
      .on('end', () => resolve(h.digest('hex')))
      .on('error', reject);
  });
}

/** Download with Range resume. Never logs the signed URL - it carries a token. */
async function download(store, file, destDir) {
  const dest = path.join(destDir, file.name);
  const part = dest + '.part';

  let from = fs.existsSync(part) ? fs.statSync(part).size : 0;
  if (!from && fs.existsSync(dest)) from = fs.statSync(dest).size;

  const url = await store.downloadUrl(file);
  const headers = from > 0 ? { Range: `bytes=${from}-` } : {};
  const res = await fetch(url, { headers, redirect: 'follow' });

  if (from > 0 && res.status === 200) {
    // Server ignored the Range request; start the file again.
    fs.rmSync(part, { force: true });
    from = 0;
  }
  if (!res.ok && res.status !== 206) {
    const hint =
      res.status === 403
        ? '\n    HTTP 403 usually means Meta does not consider you entitled yet.\n' +
          '    For a free app: open it once on meta.com and press Get to add it\n' +
          '    to your library, then run this again.'
        : '';
    throw new Error(`download of ${file.name} failed: HTTP ${res.status}${hint}`);
  }

  const total = Number(res.headers.get('content-length') ?? 0) + from;
  const out = fs.createWriteStream(part, { flags: from > 0 ? 'a' : 'w' });
  let done = from;

  try {
    for await (const chunk of res.body) {
      if (!out.write(chunk)) await new Promise((r) => out.once('drain', r));
      done += chunk.length;
      if (total) {
        const pct = ((done / total) * 100).toFixed(0);
        process.stdout.write(`\r    ${pct.padStart(3)}%  ${human(done)} / ${human(total)}   `);
      }
    }
  } finally {
    out.end();
    await new Promise((r) => out.once('close', r));
  }
  process.stdout.write('\r' + ' '.repeat(60) + '\r');

  if (file.size && fs.statSync(part).size !== file.size) {
    throw new Error(
      `size mismatch for ${file.name}: got ${fs.statSync(part).size}, expected ${file.size}`
    );
  }

  fs.renameSync(part, dest);
  const digest = await sha256(dest);
  console.log(`    + ${file.name.padEnd(42)} ${human(file.size).padStart(9)}  sha256:${digest.slice(0, 16)}…`);
  return dest;
}

// ------------------------------------------------------------------- main

async function doFetch(args) {
  const store = new QuestStore(requireToken());
  const id = appId(args.app);

  const builds = await store.builds(id);
  const build = args.build
    ? builds.find((b) => String(b.id) === String(args.build))
    : builds[0];

  if (!build) {
    console.error(c.red(`No build ${args.build} for ${id}.`));
    console.error(c.dim('  Available: ' + builds.map((b) => b.id).join(', ')));
    process.exit(1);
  }

  console.log(c.bold(`Build ${build.id}`));
  console.log(c.dim(`  version ${build.version ?? '?'}  (versionCode ${build.version_code ?? '?'})`));
  console.log(c.dim(`  platform ${build.platform ?? '?'}`));

  const plan = await store.plan(id, build.id);
  const total = plan.files.reduce((n, f) => n + (f.size || 0), 0);
  console.log(c.dim(`  package ${plan.package}`));

  console.log(`\n  ${c.bold('Plan')}`);
  for (const f of plan.files) {
    console.log(`    ${f.kind.padEnd(6)} ${f.name.padEnd(42)} ${human(f.size).padStart(9)}`);
  }
  console.log(`    ${'total'.padEnd(48)} ${human(total).padStart(9)}`);

  if (args.dryRun) {
    console.log(c.dim('\n  --dry-run: nothing downloaded.'));
    return 0;
  }

  if (!args.yes) {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const answer = await rl.question(
      c.yellow(`\n  Download ${human(total)}? [y/N] `)
    );
    rl.close();
    if (!/^y(es)?$/i.test(answer.trim())) {
      console.log(c.dim('  Cancelled.'));
      return 0;
    }
  }

  const gameDir = path.join(args.out ?? GAMES_DIR, plan.package);
  const dataDir = path.join(gameDir, plan.package);
  fs.mkdirSync(dataDir, { recursive: true });

  console.log(`\n  ${c.bold('Downloading')}`);
  for (const f of plan.files) {
    const destDir = f.kind === 'apk' ? gameDir : dataDir;
    const fetched = await download(store, f, destDir);
    if (f.kind === 'apk') {
      // FramePort accepts any APK name; the package name is tidier.
      const named = path.join(gameDir, `${plan.package}.apk`);
      if (fetched !== named) fs.renameSync(fetched, named);
    }
  }

  console.log(c.green('\n  Done.'));
  console.log(c.dim(`  ${gameDir}`));
  console.log(
    '\n  Next:\n' +
      c.dim(`    python3 tools/apk_preflight.py ${path.join(gameDir, plan.package + '.apk')}\n`),
    c.dim('    then set up the Frame and scan this folder in FramePort.')
  );
  return 0;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || (!args.login && !args.list && !args.callback && !args.app)) {
    console.log(HELP.trim());
    return args.help ? 0 : 1;
  }

  if (args.login || args.callback) return doLogin(args);

  const store = new QuestStore(requireToken());

  if (args.list) {
    const lib = await store.library();
    console.log(c.bold(`${lib.name} — ${lib.games.length} Quest app${lib.games.length === 1 ? '' : 's'}`));
    if (lib.partial) console.log(c.yellow('  (library was paginated; some apps are not shown)'));
    for (const g of lib.games) {
      console.log(`  ${String(g.id).padEnd(16)} ${g.name}`);
    }
    return 0;
  }

  if (args.app) return doFetch(args);
  return 0;
}

main()
  .then((code) => process.exit(code ?? 0))
  .catch((err) => {
    console.error(c.red(`\n${err.message}`));
    process.exit(1);
  });