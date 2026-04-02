require("dotenv").config();

const { Telegraf } = require("telegraf");
const { initDb, pool, query, withTransaction } = require("./db");
const {
  parseBulkMapText,
  replaceTeamMap,
  clearTeamMap,
  loadTeamMap,
  upsertReminderSettings,
  loadReminderSettings,
  registerReminderRun,
} = require("./league/store");
const {
  syncChallenge,
  replaceDebts,
  loadCurrentDebts,
  loadRoundDebts,
  findUnmappedTeams,
  saveChallengeSource,
  loadChallengeSource,
  setSyncEnabled,
} = require("./league/sync");
const {
  buildDebtsPost,
  buildDebtsRoundPost,
  buildDebtorSummary,
  buildReminderMessage,
  buildDebtorSummaryMessage,
} = require("./league/format");
const { dueSlots } = require("./league/reminders");

const token = process.env.BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN;
if (!token) {
  throw new Error(
    "Bot token is missing. Set BOT_TOKEN or TELEGRAM_BOT_TOKEN in environment variables."
  );
}

const adminIds = new Set(
  String(process.env.ADMIN_IDS || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean)
);

const bot = new Telegraf(token);

function isAllowedChat(ctx) {
  const chatType = String(ctx.chat?.type || "");
  if (chatType === "group" || chatType === "supergroup") {
    return true;
  }
  if (chatType === "private") {
    return isAdmin(ctx);
  }
  return false;
}

bot.use(async (ctx, next) => {
  if (!isAllowedChat(ctx)) {
    return;
  }
  return next();
});

function isAdmin(ctx) {
  return adminIds.has(String(ctx.from?.id || ""));
}

function commandArgs(ctx) {
  const text = String(ctx.message?.text || "");
  const index = text.indexOf(" ");
  return index === -1 ? "" : text.slice(index + 1).trim();
}

function commandPayload(ctx) {
  const text = String(ctx.message?.text || "");
  const index = text.indexOf("\n");
  return index === -1 ? "" : text.slice(index + 1).trim();
}

function parseSyncInput(input) {
  const parts = input.split(/\s+/).filter(Boolean);
  const sourceUrl = parts[0] || "";
  const maxRound = Number(parts[1]);
  return {
    sourceUrl,
    maxRound: Number.isInteger(maxRound) && maxRound > 0 ? maxRound : null,
  };
}

async function sendSyncBundle(chatId, prefix, sourceUrl, maxRound) {
  const debts = await loadCurrentDebts(query);
  const unmapped = await findUnmappedTeams(query);

  await bot.telegram.sendMessage(
    chatId,
    `${prefix}\nИсточник: ${sourceUrl}\nДо тура: ${maxRound}\nТекущих долгов: ${debts.length}`
  );

  if (!unmapped.length) {
    await bot.telegram.sendMessage(chatId, "Команд без маппинга нет.");
  } else {
    await bot.telegram.sendMessage(
      chatId,
      `Команды без маппинга\n\n${unmapped
        .map((row) => `- ${row.team_raw}`)
        .join("\n")}`
    );
  }

  await bot.telegram.sendMessage(chatId, buildDebtsPost(debts));
}

async function sendReminderMessage(chatId, customText) {
  const debts = await loadCurrentDebts(query);
  const mapRows = await loadTeamMap(query);
  const summary = buildDebtorSummary(debts, mapRows);
  const message = buildReminderMessage(summary, customText);

  if (!message) {
    await bot.telegram.sendMessage(
      chatId,
      "Напоминание: игроков с долгами больше 2 не найдено."
    );
    return;
  }

  await bot.telegram.sendMessage(chatId, message);
}

async function runReminderTick() {
  const slots = dueSlots(new Date());
  if (!slots.length) {
    return;
  }

  const settings = await loadReminderSettings(query);
  if (!settings.chat_id) {
    return;
  }

  for (const slot of slots) {
    if (slot.type === "daily" && !settings.daily_enabled) {
      continue;
    }
    if (slot.type === "hourly" && !settings.hourly_enabled) {
      continue;
    }

    const isNewSlot = await registerReminderRun(query, slot.key, slot.type);
    if (!isNewSlot) {
      continue;
    }

    await sendReminderMessage(
      settings.chat_id,
      slot.type === "hourly" ? settings.hourly_text : null
    );
  }
}

bot.command("league_map_bulk", async (ctx) => {
  if (!isAdmin(ctx)) {
    await ctx.reply("Команда доступна только администраторам.");
    return;
  }

  const payload = commandPayload(ctx) || commandArgs(ctx);
  const rows = parseBulkMapText(payload);

  if (!rows.length) {
    await ctx.reply("Формат: /league_map_bulk и далее строки `Команда - @username`.");
    return;
  }

  await withTransaction(async (client) => {
    await replaceTeamMap(client, rows);
  });

  await ctx.reply(`Маппинг заменен. Записей: ${rows.length}.`);
});

bot.command("league_map_show", async (ctx) => {
  const rows = await loadTeamMap(query);
  if (!rows.length) {
    await ctx.reply("Маппинг пуст.");
    return;
  }

  await ctx.reply(
    `Текущие привязки\n\n${rows
      .map((row) => `- ${row.team_name_raw} - @${row.telegram_username}`)
      .join("\n")}`
  );
});

bot.command("league_map_clear", async (ctx) => {
  if (!isAdmin(ctx)) {
    await ctx.reply("Команда доступна только администраторам.");
    return;
  }
  await clearTeamMap(query);
  await ctx.reply("Привязки очищены.");
});

bot.command("league_sync_challenge", async (ctx) => {
  if (!isAdmin(ctx)) {
    await ctx.reply("Команда доступна только администраторам.");
    return;
  }

  const { sourceUrl, maxRound } = parseSyncInput(commandArgs(ctx));
  if (!sourceUrl || !maxRound) {
    await ctx.reply("Формат: /league_sync_challenge [url] [N]");
    return;
  }

  const chatId = ctx.chat?.id;
  const entries = await syncChallenge(sourceUrl, maxRound);

  await withTransaction(async (client) => {
    await replaceDebts(client, entries);
    await saveChallengeSource(client.query.bind(client), sourceUrl, maxRound, true, chatId);
  });

  await sendSyncBundle(chatId, "Синк выполнен.", sourceUrl, maxRound);
});

bot.command("league_sync_now", async (ctx) => {
  if (!isAdmin(ctx)) {
    await ctx.reply("Команда доступна только администраторам.");
    return;
  }

  const source = await loadChallengeSource(query);
  if (!source?.source_url) {
    await ctx.reply("Источник не настроен. Используйте /league_sync_challenge.");
    return;
  }
  if (!source.enabled) {
    await ctx.reply("Source синка выключен. Включите /league_sync_challenge заново.");
    return;
  }

  const requestedRound = Number(commandArgs(ctx));
  const maxRound =
    Number.isInteger(requestedRound) && requestedRound > 0
      ? requestedRound
      : source.max_round;

  if (!maxRound) {
    await ctx.reply("Не указан тур. Формат: /league_sync_now [N]");
    return;
  }

  const chatId = ctx.chat?.id;
  const entries = await syncChallenge(source.source_url, maxRound);

  await withTransaction(async (client) => {
    await replaceDebts(client, entries);
    await saveChallengeSource(
      client.query.bind(client),
      source.source_url,
      maxRound,
      true,
      chatId
    );
  });

  await sendSyncBundle(chatId, "Повторный синк выполнен.", source.source_url, maxRound);
});

bot.command("league_sync_off", async (ctx) => {
  if (!isAdmin(ctx)) {
    await ctx.reply("Команда доступна только администраторам.");
    return;
  }
  await setSyncEnabled(query, false);
  await ctx.reply("Синк source отключен.");
});

bot.command("league_debts_show", async (ctx) => {
  const debts = await loadCurrentDebts(query);
  const mapRows = await loadTeamMap(query);
  const summary = buildDebtorSummary(debts, mapRows);
  await ctx.reply(buildDebtorSummaryMessage(summary));
});

bot.command("league_debts_round", async (ctx) => {
  const roundNo = Number(commandArgs(ctx));
  if (!Number.isInteger(roundNo) || roundNo <= 0) {
    await ctx.reply("Формат: /league_debts_round [N]");
    return;
  }
  const rows = await loadRoundDebts(query, roundNo);
  await ctx.reply(buildDebtsRoundPost(rows, roundNo));
});

bot.command("league_reminder_on", async (ctx) => {
  if (!isAdmin(ctx)) {
    await ctx.reply("Команда доступна только администраторам.");
    return;
  }

  await upsertReminderSettings(query, {
    dailyEnabled: true,
    chatId: ctx.chat?.id,
  });

  await ctx.reply("Daily напоминания включены: 09:00, 15:00, 20:00 МСК.");
});

bot.command("league_reminder_off", async (ctx) => {
  if (!isAdmin(ctx)) {
    await ctx.reply("Команда доступна только администраторам.");
    return;
  }

  await upsertReminderSettings(query, { dailyEnabled: false });
  await ctx.reply("Daily напоминания выключены.");
});

bot.command("league_reminder_now", async (ctx) => {
  const settings = await loadReminderSettings(query);
  if (!settings.chat_id) {
    await ctx.reply("Сначала вызовите /league_reminder_on в нужном чате.");
    return;
  }

  await sendReminderMessage(settings.chat_id, settings.hourly_text);

  if (settings.chat_id !== ctx.chat?.id) {
    await ctx.reply("Напоминание отправлено в сохраненный chat_id.");
  }
});

bot.command("league_reminder_hourly_on", async (ctx) => {
  if (!isAdmin(ctx)) {
    await ctx.reply("Команда доступна только администраторам.");
    return;
  }

  await upsertReminderSettings(query, {
    hourlyEnabled: true,
    hourlyText: commandArgs(ctx) || "Напоминание по долгам лиги",
    chatId: ctx.chat?.id,
  });

  await ctx.reply("Hourly напоминания включены (каждый час в :00). ");
});

bot.command("league_reminder_hourly_off", async (ctx) => {
  if (!isAdmin(ctx)) {
    await ctx.reply("Команда доступна только администраторам.");
    return;
  }

  await upsertReminderSettings(query, { hourlyEnabled: false });
  await ctx.reply("Hourly напоминания выключены.");
});

bot.catch((error, ctx) => {
  console.error("Bot error", error);
  ctx?.reply("Произошла ошибка. Проверьте формат команды.").catch(() => {});
});

async function start() {
  await initDb();
  await bot.launch();

  setInterval(() => {
    runReminderTick().catch((error) => {
      console.error("Reminder tick error", error);
    });
  }, 30 * 1000);

  console.log("Bot started");
}

start().catch((error) => {
  console.error("Startup failed", error);
  process.exit(1);
});

process.once("SIGINT", async () => {
  bot.stop("SIGINT");
  await pool.end();
});

process.once("SIGTERM", async () => {
  bot.stop("SIGTERM");
  await pool.end();
});
