/**
 * Content Script: Product ID Detector
 * 
 * Подход A: автодетект ID товаров (5 стратегий)
 * Подход B: ручной CSS-селектор (picker mode) — fallback
 */

const DETECTION_STRATEGIES = [
  {
    name: "data-attributes",
    detect() {
      const selectors = ["[data-product-id]","[data-item-id]","[data-id]","[data-sku]","[data-article]","[data-product]"];
      for (const sel of selectors) {
        const els = document.querySelectorAll(sel);
        if (els.length >= 3) {
          const attr = sel.match(/\[(.+)\]/)[1];
          const ids = [...els].map(el => el.getAttribute(attr)).filter(Boolean);
          if (ids.length >= 3) return { ids, selector: sel, attr };
        }
      }
      return null;
    },
  },
  {
    name: "product-links",
    detect() {
      const links = document.querySelectorAll('a[href*="/product"], a[href*="/p/"], a[href*="/item/"], a[href*="/catalog/"]');
      const idPattern = /\/(?:product|p|item|catalog)[/-](\d{3,})/;
      const ids = [];
      for (const a of links) { const m = a.href.match(idPattern); if (m) ids.push(m[1]); }
      const unique = [...new Set(ids)];
      if (unique.length >= 3) return { ids: unique, selector: "link-parse", attr: "href" };
      return null;
    },
  },
  {
    name: "json-ld",
    detect() {
      const scripts = document.querySelectorAll('script[type="application/ld+json"]');
      const ids = [];
      for (const script of scripts) {
        try {
          const data = JSON.parse(script.textContent);
          const items = data["@graph"] || (Array.isArray(data) ? data : [data]);
          for (const item of items) {
            if (item["@type"] === "Product" && item.sku) ids.push(String(item.sku));
            if (item["@type"] === "Product" && item.productID) ids.push(String(item.productID));
            if (item["@type"] === "ItemList" && item.itemListElement) {
              for (const li of item.itemListElement) {
                if (li.item?.productID) ids.push(String(li.item.productID));
                if (li.item?.sku) ids.push(String(li.item.sku));
              }
            }
          }
        } catch (e) {}
      }
      if (ids.length >= 3) return { ids, selector: "json-ld", attr: "sku" };
      return null;
    },
  },
  {
    name: "dataLayer",
    detect() {
      if (!window.dataLayer) return null;
      const ids = [];
      for (const entry of window.dataLayer) {
        const products = entry?.ecommerce?.impressions || entry?.ecommerce?.items || entry?.ecommerce?.products || entry?.items || [];
        for (const p of products) { const id = p.id || p.item_id || p.productId || p.sku; if (id) ids.push(String(id)); }
      }
      const unique = [...new Set(ids)];
      if (unique.length >= 3) return { ids: unique, selector: "dataLayer", attr: "id" };
      return null;
    },
  },
  {
    name: "diginetica-tracking",
    detect() {
      const els = document.querySelectorAll("[data-search-product-id], [data-digi-id], .digi-product[data-id]");
      if (els.length >= 3) {
        const ids = [...els].map(el => el.getAttribute("data-search-product-id") || el.getAttribute("data-digi-id") || el.getAttribute("data-id")).filter(Boolean);
        if (ids.length >= 3) return { ids, selector: "diginetica-tracking", attr: "data-search-product-id" };
      }
      return null;
    },
  },
];

function autoDetectProductIds(count = 10) {
  for (const s of DETECTION_STRATEGIES) {
    const r = s.detect();
    if (r) return { ids: r.ids.slice(0, count), method: `auto:${s.name}`, detail: s.name };
  }
  return null;
}

function detectWithSelector(selector, count = 10) {
  try {
    const els = document.querySelectorAll(selector);
    const ids = [];
    for (const el of els) {
      const id = el.getAttribute("data-product-id") || el.getAttribute("data-item-id") || el.getAttribute("data-id") || el.getAttribute("data-sku") || el.dataset?.productId || el.dataset?.id;
      if (id) { ids.push(id); continue; }
      const link = el.querySelector("a[href]");
      if (link) { const m = link.href.match(/\/(?:product|p|item)[/-](\d{3,})/); if (m) ids.push(m[1]); }
    }
    if (ids.length > 0) return { ids: [...new Set(ids)].slice(0, count), method: "manual:selector", detail: selector };
  } catch (e) {}
  return null;
}

// Picker mode
let pickerActive = false, pickerOverlay = null, lastHighlighted = null;

function startSelectorPicker() {
  pickerActive = true;
  pickerOverlay = document.createElement("div");
  pickerOverlay.id = "digi-checkup-picker";
  pickerOverlay.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:999999;background:rgba(15,23,42,0.85);color:#fff;padding:14px 20px;font-size:14px;font-family:system-ui;text-align:center;backdrop-filter:blur(4px)";
  pickerOverlay.innerHTML = '<b>🎯 Кликните на любую карточку товара</b><button id="digi-picker-cancel" style="margin-left:20px;padding:4px 14px;border-radius:6px;border:1px solid rgba(255,255,255,0.3);background:transparent;color:#fff;cursor:pointer">Отмена</button>';
  document.body.appendChild(pickerOverlay);
  document.getElementById("digi-picker-cancel").onclick = stopPicker;
  document.addEventListener("mouseover", onHover, true);
  document.addEventListener("click", onPick, true);
}

function stopPicker() {
  pickerActive = false;
  pickerOverlay?.remove();
  document.removeEventListener("mouseover", onHover, true);
  document.removeEventListener("click", onPick, true);
  if (lastHighlighted) { lastHighlighted.style.outline = ""; lastHighlighted = null; }
}

function onHover(e) {
  if (!pickerActive) return;
  if (lastHighlighted) lastHighlighted.style.outline = "";
  e.target.style.outline = "3px solid #3B82F6";
  lastHighlighted = e.target;
}

function onPick(e) {
  if (!pickerActive) return;
  e.preventDefault();
  e.stopPropagation();
  const card = findCard(e.target);
  const selector = buildSelector(card);
  stopPicker();
  chrome.runtime.sendMessage({ type: "SET_SELECTOR", selector });
  chrome.runtime.sendMessage({ type: "SELECTOR_PICKED", selector, count: document.querySelectorAll(selector).length });
}

function findCard(el) {
  let cur = el;
  for (let i = 0; i < 8; i++) {
    if (!cur.parentElement) break;
    const siblings = cur.parentElement.children;
    if (siblings.length >= 3 && [...siblings].filter(s => s.tagName === cur.tagName).length >= 3) return cur;
    cur = cur.parentElement;
  }
  return el;
}

function buildSelector(el) {
  const cls = [...el.classList].filter(c => !c.includes("digi") && !c.includes("highlight"));
  if (cls.length > 0) { const s = `.${cls[0]}`; if (document.querySelectorAll(s).length >= 3) return s; }
  if (el.parentElement) {
    const pc = [...el.parentElement.classList].filter(c => c.length > 0);
    if (pc.length > 0) { const s = `.${pc[0]} > ${el.tagName.toLowerCase()}`; if (document.querySelectorAll(s).length >= 3) return s; }
  }
  return el.tagName.toLowerCase();
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "GET_PRODUCT_IDS") {
    const r = autoDetectProductIds(msg.count || 10);
    if (r) { sendResponse(r); return true; }
    if (msg.selector) { const m = detectWithSelector(msg.selector, msg.count || 10); if (m) { sendResponse(m); return true; } }
    sendResponse({ ids: [], method: "none" });
    return true;
  }
  if (msg.type === "START_PICKER") { startSelectorPicker(); sendResponse({ ok: true }); return true; }
  if (msg.type === "STOP_PICKER") { stopPicker(); sendResponse({ ok: true }); return true; }
});
