import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

class FakeStatement {
  constructor(sql) { this.sql = sql; this.params = []; }
  bind(...params) { this.params = params; return this; }
  async first() {
    if (this.sql.includes("SELECT * FROM users")) return null;
    if (this.sql.includes("COUNT(*) AS users")) return { users: 0 };
    if (this.sql.includes("COUNT(*) AS records")) return { records: 0 };
    return null;
  }
  async run() { return { meta: { changes: 1 } }; }
  async all() { return { results: [] }; }
}

class FakeDB {
  prepare(sql) { return new FakeStatement(sql); }
}

test("/start shows the complete persistent menu, including while state is new", async () => {
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
