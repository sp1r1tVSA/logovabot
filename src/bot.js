require("dotenv").config();
const { Telegraf } = require("telegraf");

const token = process.env.BOT_TOKEN;

if (!token) {
  throw new Error("BOT_TOKEN is missing. Add it to your .env file.");
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
