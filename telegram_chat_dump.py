"""
Сборщик сообщений/постов из Telegram-чата или канала турнира.

Режимы:
    --mode chat     переписка чата: юзернейм, дата, текст, реплаи, медиа
                    (удобно собрать манеру общения участников для промта)
    --mode channel  посты канала: дата, подпись, медиа
                    (результаты матчей, таблицы, анонсы)

Использование:
    python telegram_chat_dump.py <username_or_link> --mode chat --limit 15000
    python telegram_chat_dump.py <username_or_link> --mode channel --with-media

Учётка для входа: my.telegram.org -> API development tools -> api_id / api_hash.
При первом запуске потребуется код из Telegram и (если включена) пароль 2FA.
"""
import argparse
import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.tl.types import MessageService, MessageMediaPhoto, MessageMediaDocument


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Собрать сообщения/посты из Telegram-чата или канала")
    parser.add_argument("target", help="Юзернейм (@kpl_liga) или ссылка (https://t.me/kpl_liga)")
    parser.add_argument("--mode", choices=["chat", "channel"], default="chat", help="chat = переписка, channel = посты канала")
    parser.add_argument("--limit", type=int, default=0, help="Максимум сообщений (0 = все)")
    parser.add_argument("--out", default=None, help="Имя выходного файла (по умолчанию chat_dump.txt / channel_posts.txt)")
    parser.add_argument("--with-media", action="store_true", help="Скачивать фото/видео в папку media/")
    parser.add_argument("--api-id", default=os.getenv("TELEGRAM_API_ID"), help="api_id (или переменная TELEGRAM_API_ID)")
    parser.add_argument("--api-hash", default=os.getenv("TELEGRAM_API_HASH"), help="api_hash (или переменная TELEGRAM_API_HASH)")
    return parser.parse_args()


def media_kind(msg) -> str | None:
    if isinstance(msg.media, MessageMediaPhoto) or (msg.media and hasattr(msg.media, 'photo')):
        return "фото"
    if isinstance(msg.media, MessageMediaDocument):
        doc = msg.media.document
        mimes = [getattr(a, 'mime_type', '') for a in (doc.attributes if doc else [])]
        if any(m.startswith('video') for m in mimes):
            return "видео"
        if any(m.startswith('image') for m in mimes):
            return "фото"
        return "файл"
    if msg.media:
        return "вложение"
    return None


def sender_label(msg, entity) -> str:
    if msg.sender_id:
        try:
            if msg.sender:
                if msg.sender.username:
                    return f"@{msg.sender.username}"
                name = msg.sender.first_name or ""
                last = getattr(msg.sender, "last_name", "") or ""
                return f"{name} {last}".strip()
        except Exception:
            pass
    return getattr(entity, "title", None) or "неизвестно"


async def main() -> None:
    args = parse_args()

    if not args.api_id or not args.api_hash:
        print("ОШИБКА: не указаны api_id / api_hash.")
        print("Получи их на my.telegram.org -> API development tools.")
        print("Передай как --api-id / --api-hash или переменные TELEGRAM_API_ID / TELEGRAM_API_HASH.")
        sys.exit(1)

    phone = input("Твой номер телефона (в формате +79991234567): ").strip()

    session_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "telegram_dump_session")
    client = TelegramClient(session_path, int(args.api_id), args.api_hash)
    await client.start(phone=phone)
    me = await client.get_me()
    print(f"Вошли как: {me.first_name} (@{me.username})")

    try:
        entity = await client.get_entity(args.target)
    except Exception as e:
        print(f"ОШИБКА: не удалось найти '{args.target}': {e}")
        await client.disconnect()
        sys.exit(1)

    chat_name = getattr(entity, "title", None) or args.target
    print(f"Чат/канал: {chat_name} (режим: {args.mode})")

    is_channel = getattr(entity, "megagroup", None) is False
    print(f"Тип: {'канал' if is_channel else 'чат/группа'}")

    media_dir = "media"
    if args.with_media:
        os.makedirs(media_dir, exist_ok=True)

    # Для mode=channel идём от старых к новым (все посты подряд).
    # Для mode=chat с лимитом идём от НОВЫХ к старым и потом разворачиваем,
    # чтобы получить именно последние N сообщений в хронологии.
    limit = args.limit if args.limit else None
    if args.mode == "channel":
        it = client.iter_messages(entity, reverse=True, limit=limit)
        collected = []
    else:
        it = client.iter_messages(entity, limit=limit)
        collected = []  # накапливаем (новые -> старые), потом развернём

    count = 0
    async for msg in it:
        if isinstance(msg, MessageService):
            continue

        text = (msg.message or "").strip()
        if not text and not msg.media:
            continue

        date_str = msg.date.strftime("%d.%m.%Y %H:%M") if msg.date else ""

        if args.mode == "channel":
            heading = f"[{date_str}] ПОСТ КАНАЛА:"
        else:
            heading = f"[{date_str}] {sender_label(msg, entity)}:"

        kind = media_kind(msg)
        media_note = ""
        if args.with_media and kind:
            try:
                ext = "jpg" if kind == "фото" else ("mp4" if kind == "видео" else "file")
                filename = f"{media_dir}/msg_{msg.id}.{ext}"
                await client.download_media(msg, file=filename)
                media_note = f" [скачано: {filename}]"
            except Exception as e:
                media_note = f" [ошибка скачивания: {e}]"
        elif kind:
            media_note = f" [медиа: {kind}]"

        entry_lines = []
        if args.mode == "chat" and msg.reply_to and getattr(msg.reply_to, "reply_to_msg_id", None):
            try:
                reply = await client.get_messages(entity, ids=msg.reply_to.reply_to_msg_id)
                if reply and reply.message:
                    brief = " ".join(reply.message.strip().split())[:60]
                    entry_lines.append(f"    > ответ на: {brief}")
            except Exception:
                pass

        if text:
            entry_lines.append(f"{heading} {text}{media_note}")
        else:
            entry_lines.append(f"{heading} (без текста){media_note}")

        collected.extend(entry_lines)
        count += 1
        if count % 500 == 0:
            print(f"Обработано: {count}...")

    if args.mode == "chat":
        collected.reverse()

    if not args.out:
        args.out = "channel_posts.txt" if args.mode == "channel" else "chat_dump.txt"

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(f"=== {args.mode.upper()} '{chat_name}' ===\n")
        f.write(f"=== Собрано записей: {count} ===\n\n")
        f.write("\n".join(collected))

    print(f"\nГотово! Собрано: {count}")
    print(f"Сохранено в файл: {args.out}")
    if args.with_media:
        print(f"Медиа сохранены в папку: {media_dir}/")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())