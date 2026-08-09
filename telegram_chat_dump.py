"""
Сборщик ПОСТОВ из открытого Telegram-канала турнира.

Канал публикует посты: результаты матчей, таблицы, анонсы, скриншоты.
Скрипт собирает каждый пост канала: дату, подпись (caption/текст) и пометку о медиа.

Использование:
    python telegram_chat_dump.py <username_or_link> [--limit N] [--out FILE] [--with-media]

Примеры:
    python telegram_chat_dump.py https://t.me/kpl_liga
    python telegram_chat_dump.py @kpl_liga --limit 500
    python telegram_chat_dump.py @kpl_liga --with-media

Учётка для входа: my.telegram.org -> API development tools -> api_id / api_hash.
При первом запуске потребуется код из Telegram и (если включена) пароль 2FA.
"""
import argparse
import asyncio
import datetime
import os
import sys

from telethon import TelegramClient
from telethon.tl.types import MessageService, MessageMediaPhoto, MessageMediaDocument


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Собрать посты из открытого Telegram-канала")
    parser.add_argument("channel", help="Юзернейм (@kpl_liga) или ссылка (https://t.me/kpl_liga)")
    parser.add_argument("--limit", type=int, default=0, help="Максимум постов (0 = все)")
    parser.add_argument("--out", default="channel_posts.txt", help="Имя выходного файла")
    parser.add_argument("--with-media", action="store_true", help="Дополнительно скачивать фото/видео в папку media/")
    parser.add_argument("--api-id", default=os.getenv("TELEGRAM_API_ID"), help="api_id (или переменная TELEGRAM_API_ID)")
    parser.add_argument("--api-hash", default=os.getenv("TELEGRAM_API_HASH"), help="api_hash (или переменная TELEGRAM_API_HASH)")
    return parser.parse_args()


def media_kind(msg) -> str:
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
        entity = await client.get_entity(args.channel)
    except Exception as e:
        print(f"ОШИБКА: не удалось найти канал '{args.channel}': {e}")
        await client.disconnect()
        sys.exit(1)

    chat_name = getattr(entity, "title", None) or args.channel
    print(f"Канал: {chat_name}")

    media_dir = "media"
    if args.with_media:
        os.makedirs(media_dir, exist_ok=True)

    lines = []
    count = 0

    async for msg in client.iter_messages(entity, reverse=True, limit=args.limit if args.limit else None):
        if isinstance(msg, MessageService):
            continue

        date_str = msg.date.strftime("%d.%m.%Y %H:%M") if msg.date else ""
        heading = f"[{date_str}] ПОСТ КАНАЛА:"

        text = (msg.message or "").strip()
        if not text and not msg.media:
            continue

        kind = media_kind(msg)
        if args.with_media and kind:
            try:
                filename = f"{media_dir}/post_{msg.id}.jpg"
                await client.download_media(msg, file=filename)
                media_note = f" [скачано: {filename}]"
            except Exception as e:
                media_note = f" [ошибка скачивания: {e}]"
        else:
            media_note = f" [медиа: {kind}]" if kind else ""

        if text:
            lines.append(f"{heading} {text}{media_note}")
        else:
            lines.append(f"{heading} (без текста){media_note}")

        count += 1
        if count % 100 == 0:
            print(f"Собрано постов: {count}...")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(f"=== Посты канала: {chat_name} ===\n")
        f.write(f"=== Собрано постов: {count} ===\n\n")
        f.write("\n".join(lines))

    print(f"\nГотово! Постов собрано: {count}")
    print(f"Сохранено в файл: {args.out}")
    if args.with_media:
        print(f"Медиа сохранены в папку: {media_dir}/")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())