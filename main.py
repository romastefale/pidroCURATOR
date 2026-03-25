import os
import json
import logging
import asyncio
import threading
import sqlite3
from contextlib import contextmanager
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import trafilatura
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# ================= CONFIG =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

DB_PATH = "cache.db"

logging.basicConfig(level=logging.INFO)

# ================= DATABASE =================
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                url TEXT PRIMARY KEY,
                titulo TEXT,
                resumo TEXT,
                fonte TEXT
            )
        """)
        conn.commit()

# ================= SERVER =================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass

def keep_alive():
    port = int(os.getenv("PORT", 8080))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

# ================= SCRAPER =================
def scrape(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla"})
        r.raise_for_status()

        data = trafilatura.extract(r.text, output_format="json")
        return json.loads(data) if data else None

    except Exception as e:
        logging.error(f"Scrape error: {e}")
        return None

# ================= FALLBACK =================
def fallback(text):
    try:
        return ".".join(text.replace("\n", " ").split(".")[:4])
    except:
        return text[:300]

# ================= AI =================
async def resumo_ai(title, text):
    if not OPENAI_API_KEY:
        return fallback(text)

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)

        prompt = f"""
Você é um redator profissional especializado em notícias para Telegram.

Produza um resumo jornalístico com base na notícia abaixo.

Regras:
- Um único parágrafo com 4 a 6 frases
- Comece direto pelo fato principal
- Linguagem clara, objetiva e informativa
- Inclua contexto relevante
- Inclua possíveis impactos ou desdobramentos
- Texto fluido e natural

Restrições:
- Sem emojis
- Sem hashtags
- Sem aspas
- Sem markdown
- Evite caracteres como <, >, &

Notícia:
{title}

{text[:3000]}
"""

        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.7
            ),
            timeout=15
        )

        return resp.choices[0].message.content.strip()

    except Exception as e:
        logging.error(f"AI error: {e}")
        return fallback(text)

# ================= CACHE =================
def get_cache(url):
    with get_db() as conn:
        cur = conn.execute("SELECT titulo,resumo,fonte FROM cache WHERE url=?", (url,))
        return cur.fetchone()

def save_cache(url, t, r, f):
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO cache VALUES (?,?,?,?)", (url, t, r, f))
        conn.commit()

# ================= FORMAT =================
def format_post(title, resumo, url):
    return (
        f"<b>📰 {escape(title)}</b>\n\n"
        f"<blockquote><i>{escape(resumo)}</i></blockquote>\n\n"
        f"{url}"
    )

# ================= HANDLER =================
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    url = (update.message.text or "").strip()
    if not url.startswith("http"):
        return

    cached = get_cache(url)
    if cached:
        t, r, f = cached
        await update.message.reply_text(
            format_post(t, r, url),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False
        )
        return

    msg = await update.message.reply_text("Processando...")

    data = await asyncio.to_thread(scrape, url)

    if not data or not data.get("text"):
        await msg.edit_text("Erro ao extrair.")
        return

    title = data.get("title") or "Notícia"
    fonte = data.get("sitename") or "Fonte"
    text = data.get("text") or ""

    resumo = await resumo_ai(title, text)

    save_cache(url, title, resumo, fonte)

    await msg.edit_text(
        format_post(title, resumo, url),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=False
    )

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Envie um link.")

# ================= MAIN =================
def main():
    init_db()

    threading.Thread(target=keep_alive, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    logging.info("Bot rodando...")

    app.run_polling()

if __name__ == "__main__":
    main()
