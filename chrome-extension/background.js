// Tesla Inventory Tracker — background service worker
// Runs a fetch() INSIDE a real Chrome tab every 5 minutes.
// Because it executes in Chrome's own context, Tesla sees a real browser —
// real cookies, real TLS fingerprint, real everything.

const VPS_INGEST_URL  = 'http://db.bhide.au:8080/tesla/api/ingest';
const INGEST_TOKEN    = 'edba657f94174221503e1b9fcf9702f7a3c298ff2f5012b0ab8f5ea5ed6e1688';
const MODELS          = ['my', 'm3'];
const CONDITIONS      = ['new', 'used'];
const MARKET          = 'AU';
const LANGUAGE        = 'en';
const SUPER_REGION    = 'north america';
const SCRAPE_INTERVAL = 5; // minutes

// ── Message handler (from popup) ──────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'scrapeNow') {
    runScrape().then(() => sendResponse({ ok: true }));
    return true; // keep channel open for async response
  }
});

// ── Bootstrap ─────────────────────────────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create('tesla-scrape', { periodInMinutes: SCRAPE_INTERVAL });
  log('Installed. Scraping every', SCRAPE_INTERVAL, 'minutes.');
  runScrape();
});

// Re-register alarm on service worker restart (Chrome can kill SW at any time)
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create('tesla-scrape', { periodInMinutes: SCRAPE_INTERVAL });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'tesla-scrape') runScrape();
});

// ── Main scrape loop ───────────────────────────────────────────────────────
async function runScrape() {
  const start = Date.now();
  log('Scrape run starting...');

  for (const model of MODELS) {
    for (const condition of CONDITIONS) {
      try {
        await scrapeOne(model, condition);
      } catch (err) {
        log(`ERROR ${model}/${condition}:`, err.message);
      }
      await sleep(2500); // polite gap between requests
    }
  }

  log(`Scrape run complete in ${((Date.now() - start) / 1000).toFixed(1)}s`);
  await saveLastRun();
}

async function scrapeOne(model, condition) {
  // Open a background tab on the Tesla inventory page.
  // Using the real page URL ensures Tesla sets/refreshes all cookies before our fetch().
  const url = `https://www.tesla.com/en_AU/inventory/${condition}/${model}?arrangeby=plh&range=0`;
  const tab = await chrome.tabs.create({ url, active: false });

  try {
    await waitForTabLoad(tab.id, 35000);

    // Execute the fetch INSIDE the tab — uses Chrome's real cookies + fingerprint
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: fetchTeslaInventory,
      args: [model, condition, MARKET, LANGUAGE, SUPER_REGION],
    });

    if (!result) {
      log(`${model}/${condition}: executeScript returned null`);
      return;
    }

    if (result.error) {
      log(`${model}/${condition}: API error — ${result.error} (HTTP ${result.status ?? '?'})`);
      // 412 = Akamai challenge — wait and retry once with a longer delay
      if (result.status === 412) {
        log(`${model}/${condition}: 412 — retrying after 8s...`);
        await sleep(8000);
        const [retry] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: fetchTeslaInventory,
          args: [model, condition, MARKET, LANGUAGE, SUPER_REGION],
        });
        if (!retry?.result || retry.result.error) {
          log(`${model}/${condition}: retry also failed — skipping`);
          return;
        }
        Object.assign(result, retry.result);
      } else {
        return;
      }
    }

    // Normalise: results can be array or nested object depending on AU inventory size
    let raw = result.results ?? result.data?.results ?? [];
    let vehicles = Array.isArray(raw)
      ? raw
      : Object.values(raw).filter(v => v && typeof v === 'object');

    if (!Array.isArray(vehicles)) vehicles = [];

    const total = result.total_matches_found ?? result.data?.total_matches_found ?? 0;
    log(`${model}/${condition}: ${vehicles.length} vehicles (AU total: ${total})`);
    if (vehicles.length === 0 && total > 0) {
      // Something is off — log raw keys so we can debug
      log(`${model}/${condition}: WARNING — total=${total} but 0 parsed. result keys:`, Object.keys(result));
    }

    await pushToVPS(model, condition, vehicles);
  } finally {
    try { chrome.tabs.remove(tab.id); } catch {}
  }
}

// ── This function is serialised and runs INSIDE the real Chrome tab ────────
// It must be completely self-contained — no references to outer scope.
async function fetchTeslaInventory(model, condition, market, language, superRegion) {
  const query = JSON.stringify({
    query: {
      model,
      condition,
      options: {},
      arrangeby: 'Price',
      order: 'asc',
      market,
      language,
      super_region: superRegion,
    },
    offset: 0,
    count: 50,
    outsideOffset: 0,
    outsideSearch: false,
    isFalconDeliverySelectionEnabled: true,
    version: 'v2',
  });

  const apiUrl = `https://www.tesla.com/inventory/api/v4/inventory-results?query=${encodeURIComponent(query)}`;

  try {
    const resp = await fetch(apiUrl, {
      headers: {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-AU,en;q=0.9',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
      },
      credentials: 'include', // send all cookies
    });

    if (!resp.ok) return { error: `HTTP ${resp.status}`, status: resp.status };
    return await resp.json();
  } catch (err) {
    return { error: err.message };
  }
}

// ── Push to VPS ────────────────────────────────────────────────────────────
async function pushToVPS(model, condition, vehicles) {
  const activeVins = vehicles.filter(v => v.VIN).map(v => v.VIN);
  try {
    const resp = await fetch(VPS_INGEST_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Ingest-Token': INGEST_TOKEN,
      },
      body: JSON.stringify({ model, condition, vehicles, active_vins: activeVins }),
    });
    const ack = await resp.json();
    log(`VPS ack [${model}/${condition}]:`, JSON.stringify(ack));
  } catch (err) {
    log(`VPS push failed [${model}/${condition}]:`, err.message);
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────
function waitForTabLoad(tabId, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error(`Tab ${tabId} load timeout`));
    }, timeoutMs);

    function listener(id, info) {
      if (id !== tabId || info.status !== 'complete') return;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      // 4s for Tesla's JS + Akamai's bot-validation to fully complete before we fetch
      setTimeout(resolve, 4000);
    }

    chrome.tabs.onUpdated.addListener(listener);

    // Already loaded?
    chrome.tabs.get(tabId, (tab) => {
      if (chrome.runtime.lastError) return; // tab was closed
      if (tab?.status === 'complete') {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        setTimeout(resolve, 4000);
      }
    });
  });
}

async function saveLastRun() {
  await chrome.storage.local.set({ lastRun: new Date().toISOString() });
}

function log(...args) {
  console.log('[Tesla Tracker]', new Date().toLocaleTimeString('en-AU'), ...args);
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}
