import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve, sep } from 'node:path';
const here = fileURLToPath(new URL('.', import.meta.url));
const docs = process.env.LAB_DESK_ASSET_DIR
  ? pathToFileURL(resolve(process.env.LAB_DESK_ASSET_DIR) + sep) : new URL('../docs/', import.meta.url);
const bundle = new URL('bundle/', import.meta.url);
await mkdir(bundle, { recursive: true });
await cp(docs, bundle, { recursive: true });
const config = JSON.parse(await readFile(here + '../volatility_lab/cloud/apps_script/sync_config.json', 'utf8'));
let html = await readFile(new URL('index.html', bundle), 'utf8');
function replaceOnce(original, replacement) {
  if (html.split(original).length !== 2) throw new Error('Private build could not locate desk integration point');
  html = html.replace(original, replacement);
}
replaceOnce(config.web_app_url, '/api/snapshot');
replaceOnce('https://raw.githubusercontent.com/pankagsaini/volatility-lab/paper-feed/paper-ledger.json', '/api/paper-ledger');
replaceOnce("cache:'no-store',credentials:'omit'", "cache:'no-store',credentials:'same-origin'");
replaceOnce('<button type="button" id="syncDesk"', '<form action="/auth/logout" method="post"><button class="refresh-button" type="submit">Sign out</button></form><button type="button" id="syncDesk"');
await writeFile(new URL('index.html', bundle), html);
await cp(new URL('login.html', import.meta.url), new URL('login.html', bundle));
await writeFile(new URL('collector-url.txt', bundle), config.web_app_url);
