require("dotenv").config();
const { Telegraf } = require("telegraf");

const token = process.env.BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN;

if (!token) {
  throw new Error(
    "Bot token is missing. Set BOT_TOKEN or TELEGRAM_BOT_TOKEN in environment variables."
  );
}

const bot = new Telegraf(token);

bot.start((ctx) => {
  ctx.reply("Hello! I am your Telegram bot.");
});

bot.help((ctx) => {
  ctx.reply("Available commands: /start, /help, /ping");
});

bot.command("ping", (ctx) => {
  ctx.reply("pong");
});

bot.on("text", (ctx) => {
  ctx.reply(`You said: ${ctx.message.text}`);
});

bot.launch();

process.once("SIGINT", () => bot.stop("SIGINT"));
process.once("SIGTERM", () => bot.stop("SIGTERM"));
