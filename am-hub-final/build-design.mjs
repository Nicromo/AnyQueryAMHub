// build-design.mjs — bundle JSX into a single ES5-compatible IIFE.
//
// Порядок важен: icons → primitives → shell → pages.
// Внутри каждого файла в конце: Object.assign(window, {...}) — так компоненты
// становятся доступны друг другу и нашему mount-скрипту в app.html.
//
// Результат: static/design/dist/bundle.js — один файл, без Babel в рантайме.

import { build } from "esbuild";
import { readFile, writeFile, mkdir, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.join(__dirname, "static", "design");
const OUT_DIR = path.join(SRC, "dist");
const OUT_FILE = path.join(OUT_DIR, "bundle.js");

const ORDER = [
  "icons.jsx",
  "primitives.jsx",
  "shell.jsx",
  "extension.jsx",     // ExtensionPopup — используется в PageExtInstall из page_more
  "page_today.jsx",
  "page_clients.jsx",
  "page_hub.jsx",
  "page_analytics.jsx",
  "page_more.jsx",
  "page_hypotheses.jsx",
  "page_broadcast.jsx",
  "page_jira.jsx",
  "page_gdrive.jsx",
  "page_auto_followups.jsx",
  "page_context.jsx",
  "page_prep.jsx",
  "page_debug.jsx",
];

// 1) конкатенация исходников (через IIFE-обёртку каждому файлу, чтобы
//    локальные const не конфликтовали между файлами)
const chunks = [];
for (const name of ORDER) {
  const full = path.join(SRC, name);
  if (!existsSync(full)) {
    console.error(`[build] missing: ${full}`);
    process.exit(1);
  }
  const code = await readFile(full, "utf8");
  chunks.push(`/* ─── ${name} ───────────────────────────────── */\n;(function(){\n${code}\n})();`);
}

const combined = chunks.join("\n\n");

// 2) транспиляция JSX → JS (без bundling, React берём из <script> тега)
const result = await build({
  stdin: {
    contents: combined,
    loader: "jsx",
    sourcefile: "design-combined.jsx",
    resolveDir: SRC,
  },
  jsx: "transform",
  jsxFactory: "React.createElement",
  jsxFragment: "React.Fragment",
  target: ["es2018"],
  minify: true,
  bundle: false,
  write: false,
  format: "iife",
  logLevel: "info",
});

// 3) запись
await mkdir(OUT_DIR, { recursive: true });
const out = result.outputFiles[0].text;
await writeFile(OUT_FILE, out, "utf8");

const { size } = await stat(OUT_FILE);
console.log(`[build] wrote ${OUT_FILE} (${(size / 1024).toFixed(1)} KB)`);
