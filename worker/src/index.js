const BTN_ADD = "➕ Додати адресу";
const BTN_CHANGE = "✏️ Змінити адресу";
const BTN_STATUS = "📊 Мій графік";
const BTN_STOP = "🔕 Вимкнути сповіщення";
const BTN_RESUME = "🔔 Увімкнути сповіщення";
const BTN_HELP = "ℹ️ Допомога";
const BTN_CANCEL = "❌ Скасувати";

function normalize(value) {
  return String(value || "")
    .toLocaleLowerCase("uk-UA")
    .replaceAll("ё", "е")
    .replace(/[’`]/g, "'")
    .replace(/[^\p{L}\p{N}'-]+/gu, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((token) => ({
      "вулиця": "вул", "вулиці": "вул", "вул.": "вул",
      "провулок": "пров", "провулка": "пров", "пров.": "пров",
      "проспект": "просп", "просп.": "просп",
      "площа": "пл", "пл.": "пл",
    })[token] || token)
    .join(" ");
}

function mainKeyboard(user) {
  const toggle = user?.active ? BTN_STOP : BTN_RESUME;
  return {
    keyboard: [
      [{ text: BTN_ADD }, { text: BTN_CHANGE }],
      [{ text: BTN_STATUS }, { text: toggle }],
      [{ text: BTN_CANCEL }, { text: BTN_HELP }],
    ],
    resize_keyboard: true,
    is_persistent: true,
    input_field_placeholder: user?.flow === "city" ? "Введіть населений пункт" :
      user?.flow === "street" ? "Введіть назву вулиці" :
      user?.flow === "building" ? "Введіть номер будинку" : "Оберіть дію в меню",
  };
}

async function telegram(env, method, body) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error(`Telegram ${method}: ${payload.description || response.status}`);
  }
  return payload.result;
}

function logD1Usage(label, result) {
  const meta = result?.meta || {};
  console.log("D1 usage", label, {
    rows_read: Number(meta.rows_read || 0),
    rows_written: Number(meta.rows_written || 0),
    changes: Number(meta.changes || 0),
  });
}

async function sendMessage(env, chatId, text, user, extra = {}) {
  return telegram(env, "sendMessage", {
    chat_id: chatId,
    text,
    parse_mode: "HTML",
    disable_web_page_preview: true,
    reply_markup: extra.reply_markup || mainKeyboard(user),
  });
}

async function answerCallback(env, id, text = "") {
  return telegram(env, "answerCallbackQuery", { callback_query_id: id, text: text.slice(0, 180) });
}

const GITHUB_REPOSITORY = "OlegSysakSOM/toe-light-bot";
const ON_DEMAND_WORKFLOW = "on-demand-schedule.yml";
const REFRESH_WORKFLOW = "check-schedule.yml";
const CACHE_MAX_AGE_MS = 15 * 60 * 1000;

function scheduleCacheKey(addressKey, group) {
  return String(addressKey) + '|' + String(group);
}

function toUint8Array(value) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (Array.isArray(value)) return Uint8Array.from(value);
  if (value && value.buffer instanceof ArrayBuffer) {
    return new Uint8Array(value.buffer, value.byteOffset || 0, value.byteLength || value.buffer.byteLength);
  }
  return null;
}

async function sendPhoto(env, chatId, pngBytes, caption, user) {
  const bytes = toUint8Array(pngBytes);
  if (!bytes || bytes.length < 8) throw new Error('Cached PNG is empty or invalid');
  const form = new FormData();
  form.append('chat_id', String(chatId));
  form.append('caption', caption);
  form.append('parse_mode', 'HTML');
  form.append('reply_markup', JSON.stringify(mainKeyboard(user)));
  form.append('photo', new Blob([bytes], { type: 'image/png' }), 'schedule.png');
  const response = await fetch('https://api.telegram.org/bot' + env.TELEGRAM_BOT_TOKEN + '/sendPhoto', {
    method: 'POST',
    body: form,
  });
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    throw new Error('Telegram sendPhoto: ' + (payload.description || response.status));
  }
  return payload.result;
}

async function sendCachedSchedule(env, user, chatId) {
  if (!env.DB || !user.address_key || !user.group_name) return false;
  const row = await env.DB.prepare(
    'SELECT latest_png, latest_snapshot, latest_cached_at, latest_cached_at_ms FROM schedule_images WHERE cache_key = ?'
  ).bind(scheduleCacheKey(user.address_key, user.group_name)).first();
  if (!row) return false;
  const png = toUint8Array(row.latest_png);
  const cachedAtMs = Number(row.latest_cached_at_ms || 0);
  const age = Date.now() - cachedAtMs;
  const validSignature = png && png.length >= 8 &&
    png[0] === 137 && png[1] === 80 && png[2] === 78 && png[3] === 71 &&
    png[4] === 13 && png[5] === 10 && png[6] === 26 && png[7] === 10;
  if (!validSignature || !Number.isFinite(cachedAtMs) || cachedAtMs <= 0 ||
      age < -2 * 60 * 1000 || age > CACHE_MAX_AGE_MS) {
    return false;
  }
  const cachedAt = row.latest_cached_at || 'нещодавно';
  const caption =
    '⚡ <b>ГРАФІК ВІДКЛЮЧЕНЬ</b>\n' +
    '📍 ' + escapeHtml(user.address_label) + '\n' +
    '🔢 Черга: <b>' + escapeHtml(user.group_name) + '</b>\n' +
    '🕒 Оновлено: <b>' + escapeHtml(cachedAt) + '</b> (Київ)\n\n' +
    '📲 Актуальний графік із кешу.\nДжерело: poweron.toe.com.ua';
  await sendPhoto(env, chatId, png, caption, user);
  if (row.latest_snapshot) {
    const latestSnapshot = String(row.latest_snapshot);
    const currentSnapshot = JSON.stringify(user.snapshot || {});
    const needsUserSync = currentSnapshot !== latestSnapshot ||
      String(user.last_ok || "") !== String(cachedAt) ||
      Number(user.fail_count || 0) !== 0 ||
      Boolean(user.error_notified) ||
      Boolean(user.no_schedule_notified);
    if (needsUserSync) {
      const result = await env.DB.prepare(
        "UPDATE users SET snapshot = ?, last_ok = ?, fail_count = 0, error_notified = 0, " +
        "no_schedule_notified = 0, updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?"
      ).bind(latestSnapshot, cachedAt, String(chatId)).run();
      logD1Usage("sync cached user snapshot", result);
    }
  }
  return true;
}
async function dispatchScheduledRefresh(env) {
  const token = String(env.GITHUB_ACTIONS_TOKEN || "").trim();
  if (!token) throw new Error("Не налаштовано GITHUB_ACTIONS_TOKEN у Cloudflare Worker.");
  const response = await fetch(
    `https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/workflows/${REFRESH_WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        "Accept": "application/vnd.github+json",
        "Authorization": `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "TOE-Public-Outage-Bot",
      },
      body: JSON.stringify({ ref: "main" }),
      signal: AbortSignal.timeout(12000),
    },
  );
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 300);
    throw new Error(`GitHub schedule workflow dispatch failed (HTTP ${response.status}): ${detail}`);
  }
}

async function dispatchFreshPng(env, chatId) {
  const token = String(env.GITHUB_ACTIONS_TOKEN || "").trim();
  if (!token) throw new Error("Не налаштовано GITHUB_ACTIONS_TOKEN у Cloudflare Worker.");
  const response = await fetch(
    `https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/workflows/${ON_DEMAND_WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        "Accept": "application/vnd.github+json",
        "Authorization": `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "TOE-Public-Outage-Bot",
      },
      body: JSON.stringify({ ref: "main", inputs: { chat_id: String(chatId) } }),
      signal: AbortSignal.timeout(12000),
    },
  );
  if (response.status !== 204) {
    const detail = (await response.text()).slice(0, 300);
    throw new Error(`GitHub Actions не прийняв запит (HTTP ${response.status}): ${detail}`);
  }
}

async function queueFreshPng(env, user, chatId, ctx) {
  try {
    if (await sendCachedSchedule(env, user, chatId)) return;
  } catch (error) {
    console.error("Cached PNG delivery failed; using on-demand fallback", error);
  }
  await sendMessage(env, chatId, "📸 Готую свіжий графік-картинку з даних Тернопільобленерго…", user);
  ctx?.waitUntil?.(
    dispatchFreshPng(env, chatId).catch(async (error) => {
      console.error("Fresh PNG dispatch failed", error);
      await sendMessage(env, chatId,
        "⚠️ Не вдалося запустити формування картинки. " +
        escapeHtml(error?.message || String(error)), user);
    }),
  );
}

function freshUser(chatId, message) {
  return {
    chat_id: String(chatId),
    first_name: message?.from?.first_name || "",
    username: message?.from?.username || "",
    active: 0,
    flow: null,
    city_input: "",
    street_input: "",
    pending_matches: [],
    address_key: "",
    address_label: "",
    group_name: "",
    snapshot: {},
    message_id: null,
    fail_count: 0,
    error_notified: 0,
    no_schedule_notified: 0,
    last_ok: "",
  };
}

async function getUser(env, chatId, message) {
  let row = await env.DB.prepare("SELECT * FROM users WHERE chat_id = ?").bind(String(chatId)).first();
  if (!row) row = freshUser(chatId, message);
  row.active = Boolean(row.active);
  row.error_notified = Boolean(row.error_notified);
  row.no_schedule_notified = Boolean(row.no_schedule_notified);
  try { row.pending_matches = JSON.parse(row.pending_matches || "[]"); } catch { row.pending_matches = []; }
  try { row.snapshot = JSON.parse(row.snapshot || "{}"); } catch { row.snapshot = {}; }
  return row;
}

async function saveUser(env, user) {
  const result = await env.DB.prepare(`
    INSERT INTO users (
      chat_id, first_name, username, active, flow, city_input, street_input,
      pending_matches, address_key, address_label, group_name, snapshot,
      message_id, fail_count, error_notified, no_schedule_notified, last_ok, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(chat_id) DO UPDATE SET
      first_name=excluded.first_name, username=excluded.username, active=excluded.active,
      flow=excluded.flow, city_input=excluded.city_input, street_input=excluded.street_input,
      pending_matches=excluded.pending_matches, address_key=excluded.address_key,
      address_label=excluded.address_label, group_name=excluded.group_name,
      snapshot=excluded.snapshot, message_id=excluded.message_id,
      fail_count=excluded.fail_count, error_notified=excluded.error_notified,
      no_schedule_notified=excluded.no_schedule_notified, last_ok=excluded.last_ok,
      updated_at=CURRENT_TIMESTAMP
  `).bind(
    String(user.chat_id), user.first_name || "", user.username || "", user.active ? 1 : 0,
    user.flow || null, user.city_input || "", user.street_input || "",
    JSON.stringify(user.pending_matches || []), user.address_key || "", user.address_label || "",
    user.group_name || "", JSON.stringify(user.snapshot || {}), user.message_id || null,
    Number(user.fail_count || 0), user.error_notified ? 1 : 0,
    user.no_schedule_notified ? 1 : 0, user.last_ok || "",
  ).run();
  logD1Usage("save user", result);
}

async function addressMatches(env, city, street, building) {
  const cityWords = normalize(city).split(" ").filter(Boolean)
    .filter((word) => !["с", "село", "м", "місто", "смт", "селище", "с-ще"].includes(word));
  const streetWords = normalize(street).split(" ").filter(Boolean);
  if (!cityWords.length || !streetWords.length) return [];
  // Use the leading city token so the indexed range query avoids scanning the
  // entire ~12k-row directory on every user onboarding.
  const firstToken = cityWords[0];
  const result = await env.DB.prepare(
    "SELECT source_id, city_id, city_name, city_norm, street_id, street_name, street_norm, building_names, group_gpv " +
    "FROM address_index WHERE city_norm >= ? AND city_norm < ? LIMIT 2000"
  ).bind(firstToken, `${firstToken}\uffff`).all();
  logD1Usage("address lookup", result);
  const wanted = normalize(building);
  const matches = [];
  const seen = new Set();
  for (const row of (result.results || [])) {
    if (!cityWords.every((word) => row.city_norm.includes(word))) continue;
    if (!streetWords.every((word) => row.street_norm.includes(word))) continue;
    const buildingList = String(row.building_names || "").split(",").map(normalize);
    if (!buildingList.includes(wanted)) continue;
    const group = String(row.group_gpv || "").trim();
    if (!group || ["не входить", "не входить в графік"].includes(normalize(group))) continue;
    const addressKey = `${row.city_id}/${row.street_id}/${building.trim()}`;
    const unique = `${addressKey}|${group}`;
    if (seen.has(unique)) continue;
    seen.add(unique);
    matches.push({
      address_key: addressKey,
      address_label: `${row.city_name} · ${row.street_name} · ${building.trim()}`,
      group_name: group,
    });
  }
  return matches.slice(0, 10);
}

async function activate(env, user, candidate, ctx) {
  user.active = 1;
  user.flow = null;
  user.pending_matches = [];
  user.address_key = candidate.address_key;
  user.address_label = candidate.address_label;
  user.group_name = candidate.group_name;
  user.snapshot = {};
  user.message_id = null;
  user.fail_count = 0;
  user.error_notified = 0;
  user.no_schedule_notified = 0;
  user.last_ok = "";
  await saveUser(env, user);
  await sendMessage(env, user.chat_id,
    `✅ <b>Підписку активовано!</b>\n\n📍 ${escapeHtml(user.address_label)}\n🔢 Черга: <b>${escapeHtml(user.group_name)}</b>\n\nЩоб отримати графік-картинку, натисніть «📊 Мій графік». У фоні перевірятиму зміни кожні 10 хвилин і надсилатиму нове фото лише за зміни графіка.`, user);
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>\"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
}

async function beginAddress(env, user, change) {
  user.flow = "city";
  user.city_input = "";
  user.street_input = "";
  user.pending_matches = [];
  await saveUser(env, user);
  const prefix = change
    ? "Змінимо адресу. Поточна підписка залишиться активною, доки нову адресу не буде підтверджено.\n\n"
    : "Підключимо вашу адресу.\n\n";
  await sendMessage(env, user.chat_id, `${prefix}<b>Крок 1/3.</b> Введіть населений пункт.`, user);
}

async function handleText(env, message, ctx) {
  const chat = message.chat || {};
  if (chat.type !== "private") return;
  const chatId = String(chat.id);
  const user = await getUser(env, chatId, message);
  const value = String(message.text || "").trim();
  const command = value.startsWith("/") ? value.split(/\s+/)[0].split("@")[0].toLowerCase() : "";

  if (command === "/start") {
    user.flow = null;
    user.pending_matches = [];
    await saveUser(env, user);
    const extra = user.address_key
      ? `\n\nВаша адреса: ${escapeHtml(user.address_label)}\nЧерга: <b>${escapeHtml(user.group_name)}</b>\nПідписка: ${user.active ? "активна ✅" : "вимкнена"}.`
      : "\n\nНатисніть «➕ Додати адресу», щоб підключити графік.";
    await sendMessage(env, chatId, `👋 <b>Вітаю! Це публічний бот графіків відключень Тернопільобленерго.</b>${extra}`, user);
    return;
  }
  if (value === BTN_ADD) { await beginAddress(env, user, false); return; }
  if (value === BTN_CHANGE) { await beginAddress(env, user, Boolean(user.address_key)); return; }
  if (value === BTN_CANCEL || command === "/cancel") {
    user.flow = null; user.pending_matches = [];
    await saveUser(env, user);
    await sendMessage(env, chatId, "Введення адреси скасовано. Ваша попередня підписка залишилася без змін.", user);
    return;
  }
  if (value === BTN_HELP || command === "/help") {
    await sendMessage(env, chatId, "<b>Меню бота</b>\n➕ Додати адресу — підключити графік.\n✏️ Змінити адресу — вибрати іншу.\n📊 Мій графік — побачити підписку.\n🔕/🔔 Сповіщення — вимкнути або увімкнути отримання графіка.\n❌ Скасувати — зупинити введення адреси.", user);
    return;
  }
  if (value === BTN_STATUS || command === "/status") {
    if (user.address_key) {
      await queueFreshPng(env, user, chatId, ctx);
    } else {
      await sendMessage(env, chatId, "Адресу ще не налаштовано. Натисніть «➕ Додати адресу».", user);
    }
    return;
  }
  if (value === BTN_STOP || command === "/stop") {
    if (user.address_key) { user.active = 0; user.flow = null; await saveUser(env, user); await sendMessage(env, chatId, "🔕 Сповіщення вимкнено.", user); }
    else await sendMessage(env, chatId, "Спочатку додайте адресу.", user);
    return;
  }
  if (value === BTN_RESUME || command === "/resume") {
    if (user.address_key) { user.active = 1; user.snapshot = {}; user.message_id = null; await saveUser(env, user); await sendMessage(env, chatId, "🔔 Сповіщення увімкнено. Графік надійде під час найближчої перевірки.", user); }
    else await sendMessage(env, chatId, "Спочатку додайте адресу.", user);
    return;
  }
  if (command === "/address") { await beginAddress(env, user, Boolean(user.address_key)); return; }

  if (!user.flow) {
    await sendMessage(env, chatId, "Оберіть потрібну дію кнопкою в меню нижче.", user);
    return;
  }
  if (!value) { await sendMessage(env, chatId, "Введіть текст або натисніть «❌ Скасувати».", user); return; }

  if (user.flow === "city") {
    user.city_input = value; user.flow = "street"; await saveUser(env, user);
    await sendMessage(env, chatId, "🛣️ <b>Крок 2/3.</b> Введіть назву вулиці.", user); return;
  }
  if (user.flow === "street") {
    user.street_input = value; user.flow = "building"; await saveUser(env, user);
    await sendMessage(env, chatId, "🏠 <b>Крок 3/3.</b> Введіть номер будинку.", user); return;
  }
  if (user.flow === "building") {
    const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM address_index").first();
    if (!Number(count?.n || 0)) {
      await sendMessage(env, chatId, "⚠️ Довідник адрес ще не завантажено. Адміністратор має запустити workflow синхронізації адрес у GitHub Actions. Спробуйте ще раз трохи пізніше.", user);
      return;
    }
    await sendMessage(env, chatId, "🔎 Шукаю адресу в довіднику Тернопільобленерго…", user);
    const matches = await addressMatches(env, user.city_input, user.street_input, value);
    if (!matches.length) {
      user.flow = null; await saveUser(env, user);
      await sendMessage(env, chatId, "Не знайшов адресу з такою назвою та номером будинку. Перевірте написання та натисніть «➕ Додати адресу» або «✏️ Змінити адресу», щоб повторити.", user);
      return;
    }
    if (matches.length === 1) { await activate(env, user, matches[0], ctx); return; }
    user.pending_matches = matches; user.flow = "choose"; await saveUser(env, user);
    const keyboard = matches.map((item, i) => [{
      text: `${i + 1}. ${item.address_label} · черга ${item.group_name}`.slice(0, 64),
      callback_data: `addr:${i}`,
    }]);
    await sendMessage(env, chatId, "Знайшов кілька адрес. Натисніть потрібну:", user, { reply_markup: { inline_keyboard: keyboard } });
    return;
  }
}

async function handleCallback(env, callback, ctx) {
  const callbackId = String(callback.id || "");
  const chat = callback.message?.chat || {};
  if (!callbackId) return;
  if (chat.type !== "private") { await answerCallback(env, callbackId, "Використовуйте бота в особистому чаті."); return; }
  const user = await getUser(env, chat.id, callback.message);
  const match = String(callback.data || "").match(/^addr:(\d+)$/);
  if (!match || user.flow !== "choose") { await answerCallback(env, callbackId, "Цей вибір уже неактуальний."); return; }
  const index = Number(match[1]);
  const candidate = user.pending_matches[index];
  if (!candidate) { await answerCallback(env, callbackId, "Адресу не знайдено. Спробуйте ще раз."); return; }
  await answerCallback(env, callbackId, "Адресу вибрано.");
  await activate(env, user, candidate, ctx);
}

async function handleUpdate(env, update, ctx) {
  if (update.callback_query) await handleCallback(env, update.callback_query, ctx);
  else if (update.message) await handleText(env, update.message, ctx);
}

function authorized(request, env) {
  const supplied = request.headers.get("authorization") || "";
  return supplied === `Bearer ${env.ADMIN_SECRET}` && Boolean(env.ADMIN_SECRET);
}

export default {
  async scheduled(controller, env) {
    await dispatchScheduledRefresh(env);
    console.log("Cloudflare Cron dispatched schedule refresh", controller.cron, new Date(controller.scheduledTime).toISOString());
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      const row = await env.DB.prepare("SELECT COUNT(*) AS users FROM users").first();
      const idx = await env.DB.prepare("SELECT COUNT(*) AS records FROM address_index").first();
      const cacheTable = await env.DB.prepare(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schedule_images'"
      ).first();
      return Response.json({
        ok: true, users: row?.users || 0, address_records: idx?.records || 0,
        cache_enabled: Boolean(cacheTable), cache_backend: "d1_blob",
        cron_enabled: true, version: "2.6.1",
      });
    }
    if (request.method === "POST" && url.pathname === "/internal/cache-schedule") {
      const supplied = request.headers.get("authorization") || "";
      if (!env.CACHE_UPLOAD_SECRET || supplied !== "Bearer " + env.CACHE_UPLOAD_SECRET) {
        return new Response("unauthorized", { status: 401 });
      }
      if (!env.DB) return Response.json({ ok: false, error: "D1 binding is missing" }, { status: 503 });
      try {
        const metadataHeader = request.headers.get("x-schedule-meta") || "";
        if (!metadataHeader || metadataHeader.length > 20000) {
          return Response.json({ ok: false, error: "Missing or oversized schedule metadata" }, { status: 400 });
        }
        const metadataBytes = Uint8Array.from(atob(metadataHeader), (character) => character.charCodeAt(0));
        const body = JSON.parse(new TextDecoder().decode(metadataBytes));
        const addressKey = String(body.address_key || "").trim();
        const groupName = String(body.group_name || "").trim();
        if (!addressKey || !groupName) {
          return Response.json({ ok: false, error: "Invalid address or queue metadata" }, { status: 400 });
        }
        const binary = new Uint8Array(await request.arrayBuffer());
        const pngSignature = [137, 80, 78, 71, 13, 10, 26, 10];
        if (binary.length < 8 || binary.length > 1950000 || !pngSignature.every((value, position) => binary[position] === value)) {
          return Response.json({ ok: false, error: "PNG is invalid or exceeds the 1.95 MB D1 BLOB safety limit" }, { status: 413 });
        }
        const snapshot = JSON.stringify(body.snapshot || {});
        if (snapshot.length > 10000) return Response.json({ ok: false, error: "Schedule snapshot is too large" }, { status: 400 });
        const cacheKey = scheduleCacheKey(addressKey, groupName);
        const cachedAt = String(body.cached_at || "").slice(0, 32);
        const cachedAtMs = Number(body.cached_at_ms) || Date.now();
        const sql = "INSERT INTO schedule_images (cache_key, address_key, group_name, address_label, latest_png, latest_snapshot, latest_cached_at, latest_cached_at_ms, updated_at) " +
          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) " +
          "ON CONFLICT(cache_key) DO UPDATE SET previous_png = schedule_images.latest_png, previous_snapshot = schedule_images.latest_snapshot, " +
          "previous_cached_at = schedule_images.latest_cached_at, previous_cached_at_ms = schedule_images.latest_cached_at_ms, " +
          "latest_png = excluded.latest_png, latest_snapshot = excluded.latest_snapshot, " +
          "latest_cached_at = excluded.latest_cached_at, latest_cached_at_ms = excluded.latest_cached_at_ms, " +
          "address_label = excluded.address_label, updated_at = CURRENT_TIMESTAMP";
        const result = await env.DB.prepare(sql).bind(cacheKey, addressKey, groupName, String(body.address_label || "").slice(0, 500), binary, snapshot, cachedAt, cachedAtMs).run();
        logD1Usage("cache schedule PNG", result);
        return Response.json({ ok: true, cache_backend: "d1_blob", bytes: binary.length });
      } catch (error) {
        console.error("Schedule D1 cache upload failed", error);
        return Response.json({ ok: false, error: String(error?.message || error) }, { status: 400 });
      }
    }
    if (request.method === "POST" && url.pathname === "/telegram/webhook") {
      if (request.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET) {
        return new Response("unauthorized", { status: 401 });
      }
      try {
        const update = await request.json();
        const updateId = Number(update.update_id);
        if (Number.isFinite(updateId)) {
          const seen = await env.DB.prepare("SELECT update_id FROM processed_updates WHERE update_id = ?").bind(updateId).first();
          if (seen) return Response.json({ ok: true, duplicate: true });
        }
        await handleUpdate(env, update, ctx);
        if (Number.isFinite(updateId)) {
          const result = await env.DB.prepare("INSERT OR IGNORE INTO processed_updates(update_id) VALUES (?)").bind(updateId).run();
          logD1Usage("processed Telegram update", result);
        }
        return Response.json({ ok: true });
      } catch (error) {
        console.error("Webhook update failed", error);
        return Response.json({ ok: false, error: String(error?.message || error) }, { status: 500 });
      }
    }
    if (url.pathname.startsWith("/admin/") && request.method === "POST") {
      if (!authorized(request, env)) return new Response("unauthorized", { status: 401 });
      if (url.pathname === "/admin/register-webhook") {
        const webhookUrl = `${url.origin}/telegram/webhook`;
        const result = await telegram(env, "setWebhook", {
          url: webhookUrl,
          secret_token: env.WEBHOOK_SECRET,
          allowed_updates: ["message", "callback_query"],
          drop_pending_updates: false,
        });
        return Response.json({ ok: true, webhook: webhookUrl, result });
      }
    }
    return new Response("Not found", { status: 404 });
  },
};
