// pack.mjs — сборка подписанного .crx + генерация updates.xml.
//
// Использование:
//   npm run pack                      — просто пересобрать с текущей версией
//   npm run bump:patch                — 1.0.0 → 1.0.1 + пересобрать
//   npm run bump:minor                — 1.0.0 → 1.1.0 + пересобрать
//
// После pack: коммитьте updated файлы и пушьте — Chrome пользователей
// опросит updates.xml через ~5 часов и автоматически скачает новый .crx.
//
// key.pem генерируется ОДИН РАЗ при первом запуске.
// ВАЖНО: key.pem НЕ коммитить (.gitignore). Храните его безопасно:
// если потерять — Extension ID сменится, все пользователи потеряют установленное.

import ChromeExtension from "crx";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { generateKeyPairSync } from "node:crypto";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const KEY_PATH      = path.join(__dirname, "key.pem");
const MANIFEST_PATH = path.join(__dirname, "manifest.json");
const CRX_PATH      = path.join(__dirname, "amhub-sync.crx");
const UPDATES_PATH  = path.join(__dirname, "updates.xml");

const UPDATE_URL_BASE =
  process.env.UPDATE_URL_BASE ||
  "https://raw.githubusercontent.com/Nicromo/AnyQueryAMHub/master/extension";

// Только эти файлы/папки попадают в .crx.
// Всё остальное (node_modules, key.pem, pack.mjs, zip, package*) — исключаем.
const INCLUDE = [
  "manifest.json",
  "background.js",
  "popup.html",
  "popup.js",
  "icon16.png",
  "icon48.png",
  "icon128.png",
  "fonts",
];

// ── 1. Bump версии ─────────────────────────────────────────────
function bumpVersion(version, kind) {
  const [maj, min, pat] = version.split(".").map(Number);
  if (kind === "major") return `${maj + 1}.0.0`;
  if (kind === "minor") return `${maj}.${min + 1}.0`;
  return `${maj}.${min}.${(pat || 0) + 1}`;
}

const bumpFlag = process.argv.indexOf("--bump");
const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
if (bumpFlag !== -1) {
  const kind = process.argv[bumpFlag + 1] || "patch";
  const oldV = manifest.version;
  manifest.version = bumpVersion(oldV, kind);
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n");
  console.log(`[pack] version bump: ${oldV} → ${manifest.version} (${kind})`);
}

// ── 2. Генерим key.pem если нет ────────────────────────────────
if (!fs.existsSync(KEY_PATH)) {
  console.log("[pack] key.pem не найден — генерирую новый RSA 2048");
  const { privateKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
    publicKeyEncoding:  { type: "spki",  format: "pem" },
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
  });
  fs.writeFileSync(KEY_PATH, privateKey);
  console.log("[pack] ✓ key.pem создан — СОХРАНИТЕ ЕГО БЕЗОПАСНО");
}

// ── 3. Копируем только нужное в staging-директорию ─────────────
const stage = fs.mkdtempSync(path.join(os.tmpdir(), "amhub-pack-"));
function copyRecursive(src, dst) {
  const st = fs.statSync(src);
  if (st.isDirectory()) {
    fs.mkdirSync(dst, { recursive: true });
    for (const name of fs.readdirSync(src)) {
      copyRecursive(path.join(src, name), path.join(dst, name));
    }
  } else {
    fs.copyFileSync(src, dst);
  }
}
for (const name of INCLUDE) {
  const src = path.join(__dirname, name);
  if (!fs.existsSync(src)) continue;
  copyRecursive(src, path.join(stage, name));
}

// ── 4. Пакуем .crx из staging ──────────────────────────────────
const crx = new ChromeExtension({
  privateKey: fs.readFileSync(KEY_PATH),
  codebase:   `${UPDATE_URL_BASE}/amhub-sync.crx`,
  rootDirectory: stage,
});

await crx.load(stage);
const crxBuffer = await crx.pack();
fs.writeFileSync(CRX_PATH, crxBuffer);
console.log(`[pack] ✓ ${path.basename(CRX_PATH)} (${(crxBuffer.length / 1024).toFixed(1)} KB)`);

// ── 5. Extension ID из pubkey ──────────────────────────────────
const extensionId = crx.generateAppId();
console.log(`[pack] ✓ extension id: ${extensionId}`);

// ── 6. updates.xml ─────────────────────────────────────────────
const updatesXml = `<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='${extensionId}'>
    <updatecheck codebase='${UPDATE_URL_BASE}/amhub-sync.crx' version='${manifest.version}' />
  </app>
</gupdate>
`;
fs.writeFileSync(UPDATES_PATH, updatesXml);
console.log(`[pack] ✓ ${path.basename(UPDATES_PATH)} (v${manifest.version})`);

// ── 7. Чистим staging ──────────────────────────────────────────
fs.rmSync(stage, { recursive: true, force: true });

console.log(`\n[pack] Готово. Коммитьте:`);
console.log(`  git add extension/manifest.json extension/amhub-sync.crx extension/updates.xml`);
console.log(`  git commit -m "ext: v${manifest.version}"`);
console.log(`  git push`);
console.log(`\nChrome пользователей опросит updates.xml через ~5 часов.`);
console.log(`Форсированная проверка: chrome://extensions → "Обновить".`);
