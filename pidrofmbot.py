import os
import re
import requests

from telegram import Update, InlineQueryResultPhoto
from telegram.ext import Application, InlineQueryHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise ValueError("Variável TELEGRAM_TOKEN não definida")


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.inline_query.query

    if not query:
        return

    # normaliza a busca (remove hífen, underscore e espaços extras)
    query = re.sub(r"[-_]+", " ", query)
    query = re.sub(r"\s+", " ", query).strip()

    user = update.inline_query.from_user
    user_name = user.first_name if user and user.first_name else "Someone"

    try:
        response = requests.get(
            f"https://api.deezer.com/search?q={query}",
            timeout=3
        )

        if response.status_code != 200:
            return

        data = response.json()

    except Exception:
        return

    tracks = data.get("data", [])

    if not tracks:
        return

    results = []

    for i, track in enumerate(tracks[:10]):

        try:
            title = track["title"]
            artist = track["artist"]["name"]
            album = track["album"]["title"]
            cover = track["album"]["cover_big"]

            results.append(
                InlineQueryResultPhoto(
                    id=str(i),
                    photo_url=cover,
                    thumbnail_url=cover,
                    title=f"{title} — {artist}",
                    description=f"Album: {album} • Tap to confirm",
                    caption=f"_{user_name} is listening to..._\n\n♫ Playing: {title}\n★ Artist: {artist}",
                    parse_mode="Markdown"
                )
            )

        except Exception:
            continue

    try:
        await update.inline_query.answer(results, cache_time=5)
    except Exception:
        pass


def main():

    app = Application.builder().token(TOKEN).build()

    app.add_handler(InlineQueryHandler(inline_query))

    print("Bot rodando...")

    app.run_polling()


if __name__ == "__main__":
    main()
