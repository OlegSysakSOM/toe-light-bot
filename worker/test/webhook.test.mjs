import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import worker from "../src/index.js";

class FakeStatement {
  constructor(db, sql) { this.db = db; this.sql = sql; this.params = []; }
  bind(...params) { this.params = params; return this; }
  async first() {
    if (this.sql.includes("SELECT * FROM users")) return this.db.user;
    if (this.sql.includes("FROM schedule_images")) return this.db.image;
    if (this.sql.includes("sqlite_master")) return { name: "schedule_images" };
    if (this.sql.includes("COUNT(*) AS users")) return { users: 0 };
    if (this.sql.includes("COUNT(*) AS records")) return { records: 0 };
    return null;
  }
  async run() {
    this.db.writes.push({ sql: this.sql, params: this.params });
    return { meta: { changes: 1 } };
  }
  async all() { return { results: [] }; }
}
class FakeDB {
  constructor({ user = null, image = null } = {}) {
    this.user = user;
    this.image = image;
    this.writes = [];
  }
  prepare(sql) { return new FakeStatement(this, sql); }
}
const PNG = Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10, 1, 2, 3, 4]);

test("/start shows the complete persistent menu", async () => {
  const oldFetch = globalThis.fetch;
  let sent = null;
  globalThis.fetch = async (_url, options) => {
    sent = JSON.parse(options.body);
    return new Response(JSON.stringify({ ok: true, result: { message_id: 12 } }), {
      status: 200, headers: { "content-type": "application/json" },
    });
  };
  try {
    const request = new Request("https://example.workers.dev/telegram/webhook", {
      method: "POST",
      headers: { "content-type": "application/json", "x-telegram-bot-api-secret-token": "wh-secret" },
      body: JSON.stringify({
        update_id: 42,
        message: { chat: { id: 123, type: "private" }, from: { first_name: "Test" }, text: "/start" },
      }),
    });
    const response = await worker.fetch(request, {
      DB: new FakeDB(), TELEGRAM_BOT_TOKEN: "bot-token", WEBHOOK_SECRET: "wh-secret", ADMIN_SECRET: "admin",
    });
    assert.equal(response.status, 200);
    assert.equal(sent.chat_id, "123");
    const buttons = sent.reply_markup.keyboard.flat().map((item) => item.text);
    assert.ok(buttons.includes("➕ Додати адресу"));
    assert.ok(buttons.includes("✏️ Змінити адресу"));
    assert.ok(buttons.includes("📊 Мій графік"));
    assert.ok(buttons.includes("🔔 Увімкнути сповіщення"));
    assert.ok(buttons.includes("❌ Скасувати"));
    assert.ok(buttons.includes("ℹ️ Допомога"));
  } finally {
    globalThis.fetch = oldFetch;
  }
});

test("webhook rejects requests with the wrong secret", async () => {
  const request = new Request("https://example.workers.dev/telegram/webhook", {
    method: "POST",
    headers: { "x-telegram-bot-api-secret-token": "wrong" },
    body: "{}",
  });
  const response = await worker.fetch(request, { DB: new FakeDB(), WEBHOOK_SECRET: "correct" });
  assert.equal(response.status, 401);
});

test("cache upload stores PNG bytes in D1 and rotates the previous image", async () => {
  const db = new FakeDB({
    image: {
      latest_png: Array.from(PNG),
      latest_snapshot: '{"2026-11-22":"000"}',
      latest_cached_at: "22.11.2026 12:00",
      latest_cached_at_ms: 1000,
    },
  });
  const request = new Request("https://example.workers.dev/internal/cache-schedule", {
    method: "POST",
    headers: { "content-type": "application/json", authorization: "Bearer cache-secret" },
    body: JSON.stringify({
      address_key: "22016/40637/24",
      group_name: "5.1",
      address_label: "Велика Лука · Франка · 24",
      png_base64: Buffer.from(PNG).toString("base64"),
      cached_at: "22.11.2026 12:10",
      cached_at_ms: Date.now(),
      snapshot: { "2026-11-22": "100" },
    }),
  });
  const response = await worker.fetch(request, { DB: db, CACHE_UPLOAD_SECRET: "cache-secret" });
  const payload = await response.json();
  assert.equal(response.status, 200);
  assert.equal(payload.ok, true);
  assert.equal(payload.cache_backend, "d1_blob");
  const write = db.writes.find((entry) => entry.sql.includes("INSERT INTO schedule_images"));
  assert.ok(write);
  assert.match(write.sql, /previous_png = schedule_images\.latest_png/);
  assert.ok(write.params[4] instanceof Uint8Array);
  assert.deepEqual(Array.from(write.params[4]), Array.from(PNG));
});

test("My schedule sends a cached PNG directly from D1 and advances the snapshot baseline", async () => {
  const oldFetch = globalThis.fetch;
  const db = new FakeDB({
    user: {
      chat_id: "719882819", first_name: "Oleg", username: "test", active: 1, flow: null,
      city_input: "", street_input: "", pending_matches: "[]",
      address_key: "22016/40637/24", address_label: "Велика Лука · Франка · 24",
      group_name: "5.1", snapshot: "{}", message_id: null, fail_count: 0,
      error_notified: 0, no_schedule_notified: 0, last_ok: "",
    },
    image: {
      latest_png: Array.from(PNG),
      latest_snapshot: '{"2026-11-22":"000"}',
      latest_cached_at: "22.11.2026 13:00",
      latest_cached_at_ms: Date.now(),
    },
  });
  let sendPhotoCalled = false;
  globalThis.fetch = async (url, options) => {
    if (String(url).includes("/sendPhoto")) {
      sendPhotoCalled = true;
      const photo = options.body.get("photo");
      assert.ok(photo instanceof Blob);
      assert.equal(photo.type, "image/png");
      assert.equal(photo.size, PNG.length);
      return new Response(JSON.stringify({ ok: true, result: { message_id: 88 } }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    }
    throw new Error("Unexpected network request: " + url);
  };
  try {
    const request = new Request("https://example.workers.dev/telegram/webhook", {
      method: "POST",
      headers: { "content-type": "application/json", "x-telegram-bot-api-secret-token": "wh-secret" },
      body: JSON.stringify({
        update_id: 43,
        message: { chat: { id: 719882819, type: "private" }, from: { first_name: "Oleg" }, text: "📊 Мій графік" },
      }),
    });
    const response = await worker.fetch(request, {
      DB: db, TELEGRAM_BOT_TOKEN: "bot-token", WEBHOOK_SECRET: "wh-secret",
    });
    assert.equal(response.status, 200);
    assert.equal(sendPhotoCalled, true);
    assert.ok(db.writes.some((entry) => entry.sql.includes("UPDATE users SET snapshot")));
  } finally {
    globalThis.fetch = oldFetch;
  }
});

test("Cloudflare Cron dispatches the background refresh workflow", async () => {
  const oldFetch = globalThis.fetch;
  let call = null;
  globalThis.fetch = async (url, options) => {
    call = { url: String(url), options };
    return new Response(null, { status: 204 });
  };
  try {
    await worker.scheduled(
      { cron: "0,10,20,30,40,50 * * * *", scheduledTime: Date.now() },
      { GITHUB_ACTIONS_TOKEN: "gh-test-token" },
    );
    assert.ok(call.url.includes("/actions/workflows/check-schedule.yml/dispatches"));
    assert.equal(call.options.headers.Authorization, "Bearer gh-test-token");
    assert.equal(JSON.parse(call.options.body).ref, "main");
  } finally {
    globalThis.fetch = oldFetch;
  }
});

test("Wrangler uses the requested minute marks and has no R2 binding", () => {
  const config = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  assert.match(config, /crons\s*=\s*\["0,10,20,30,40,50 \* \* \* \*"]\s*/);
  assert.doesNotMatch(config, /r2_buckets|SCHEDULE_CACHE/i);
});
