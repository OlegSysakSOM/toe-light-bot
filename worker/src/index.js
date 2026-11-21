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
  await env.DB.prepare(`
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

async function activate(env, user, candidate) {
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
    `✅ <b>Підписку активовано!</b>\n\n📍 ${escapeHtml(user.address_label)}\n🔢 Черга: <b>${escapeHtml(user.group_name)}</b>\n\nГрафік буде надіслано під час найближчого циклу перевірки (до приблизно 10 хвилин).`, user);
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

async function handleText(env, message) {
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
    let lastCheck = "ще не було";
    const meta = await env.DB.prepare("SELECT value FROM bot_meta WHERE key = 'last_schedule_check_at'").first();
    if (meta?.value) lastCheck = String(meta.value);
    const text = user.address_key
      ? `<b>Ваша підписка</b>\nСтан: ${user.active ? "🟢 активна" : "⚪ вимкнена"}\nАдреса: ${escapeHtml(user.address_label)}\nЧерга: <b>${escapeHtml(user.group_name)}</b>\nОстання перевірка графіків: ${escapeHtml(lastCheck)}`
      : "Адресу ще не налаштовано. Натисніть «➕ Додати адресу».";
    await sendMessage(env, chatId, text, user); return;
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
    if (matches.length === 1) { await activate(env, user, matches[0]); return; }
    user.pending_matches = matches; user.flow = "choose"; await saveUser(env, user);
    const keyboard = matches.map((item, i) => [{
      text: `${i + 1}. ${item.address_label} · черга ${item.group_name}`.slice(0, 64),
      callback_data: `addr:${i}`,
    }]);
    await sendMessage(env, chatId, "Знайшов кілька адрес. Натисніть потрібну:", user, { reply_markup: { inline_keyboard: keyboard } });
    return;
  }
}

async function handleCallback(env, callback) {
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
  await activate(env, user, candidate);
}

async function handleUpdate(env, update) {
  if (update.callback_query) await handleCallback(env, update.callback_query);
  else if (update.message) await handleText(env, update.message);
}

function authorized(request, env) {
  const supplied = request.headers.get("authorization") || "";
  return supplied === `Bearer ${env.ADMIN_SECRET}` && Boolean(env.ADMIN_SECRET);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      const row = await env.DB.prepare("SELECT COUNT(*) AS users FROM users").first();
      const idx = await env.DB.prepare("SELECT COUNT(*) AS records FROM address_index").first();
      return Response.json({ ok: true, users: row?.users || 0, address_records: idx?.records || 0, version: "2.2.0" });
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
        await handleUpdate(env, update);
        if (Number.isFinite(updateId)) {
          await env.DB.prepare("INSERT OR IGNORE INTO processed_updates(update_id) VALUES (?)").bind(updateId).run();
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
