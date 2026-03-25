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

# ================== CONFIG ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

DB_PATH = "cache_noticias.db"
REQUEST_TIMEOUT = 10
SCRAPE_TIMEOUT = 20
AI_TIMEOUT = 15
MAX_TEXT_INPUT = 3000
MAX_MESSAGE_LENGTH = 3800

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN não definido")

# ================== DATABASE ==================
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
            CREATE TABLE IF NOT EXISTS resumos (
                url TEXT PRIMARY KEY,
                titulo TEXT,
                resumo TEXT,
                fonte TEXT
            )
        """)
        conn.commit()

def get_cached(url):
    try:
        with get_db() as conn:
            cur = conn.execute(
                "SELECT titulo, resumo, fonte FROM resumos WHERE url=?",
                (url,)
            )
            return cur.fetchone()
    except Exception as e:
        logging.error(f"DB read error: {e}")
        return None

def save_cache(url, titulo, resumo, fonte):
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO resumos VALUES (?, ?, ?, ?)",
                (url, titulo, resumo, fonte)
            )
            conn.commit()
    except Exception as e:
        logging.error(f"DB write error: {e}")

# ================== KEEP ALIVE ==================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass

def run_server():
    port = int(os.getenv("PORT", "8080"))
    try:
        HTTPServer(("0.0.0.0", port), DummyHandler).serve_forever()
    except Exception as e:
        logging.error(f"Server error: {e}")

# ================== SCRAPER ==================
def safe_request(url):
    try:
        return requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"}
        )
    except Exception as e:
        logging.error(f"Request error: {e}")
        return None

def scrape(url):
    try:
        resp = safe_request(url)
        if not resp:
            return None

        resp.raise_for_status()

        data = trafilatura.extract(
            resp.text,
            output_format="json",
            include_comments=False
        )

        if not data:
            return None

        return json.loads(data)

    except Exception as e:
        logging.error(f"Scrape error: {e}")
        return None

# ================== FALLBACK ==================
def fallback_summary(text):
    try:
        parts = text.replace("\n", " ").split(".")
        return ".".join(parts[:3]).strip()
    except:
        return text[:300]

# ================== AI ==================
async def generate_summary(title, text):
    if not OPENAI_API_KEY:
        return fallback_summary(text)

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)

        prompt = (
            "Resuma a notícia em um único parágrafo com 3 a 4 frases, "
            "linguagem jornalística, sem emojis, sem hashtags, sem aspas:\n\n"
            f"{title}\n\n{text[:MAX_TEXT_INPUT]}"
        )

        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=220,
                temperature=0.7
            ),
            timeout=AI_TIMEOUT
        )

        return resp.choices[0].message.content.strip()

    except Exception as e:
        logging.error(f"AI error: {e}")
        return fallback_summary(text)

# ================== FORMAT ==================
def build_post(title, resumo, fonte, url):
    try:
        post = (
            f"<b>📰 {escape(title)}</b>\n\n"
            f"<blockquote><i>{escape(resumo)}</i></blockquote>\n\n"
            f"🔗 Leia mais:\n{url}\n\n"
            f"<i>{escape(fonte)}</i>"
        )

        return post[:MAX_MESSAGE_LENGTH]

    except Exception:
        return f"{title}\n\n{resumo}\n\n{url}"

# ================== HANDLER ==================
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_user.id != ADMIN_ID:
            return

        url = (update.message.text or "").strip()

        if not url.startswith("http"):
            return

        cached = get_cached(url)
        if cached:
            titulo, resumo, fonte = cached
            await update.message.reply_text(
                build_post(titulo, resumo, fonte, url),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            return

        msg = await update.message.reply_text("⚡ Processando...")

        data = await asyncio.wait_for(
            asyncio.to_thread(scrape, url),
            timeout=SCRAPE_TIMEOUT
        )

        if not data or not data.get("text"):
            await msg.edit_text("❌ Falha ao extrair conteúdo.")
            return

        title = data.get("title") or "Notícia"
        source = data.get("sitename") or "Fonte"
        text = data.get("text") or ""

        resumo = await generate_summary(title, text)

        save_cache(url, title, resumo, source)

        await msg.edit_text(
            build_post(title, resumo, source, url),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False
        )

    except asyncio.TimeoutError:
        await update.message.reply_text("⏱️ Tempo excedido.")
    except Exception as e:
        logging.error(f"Handler error: {e}")
        try:
            await update.message.reply_text("❌ Erro inesperado.")
        except:
            pass

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Envie um link de notícia.")

# ================== MAIN ==================
def main():
    init_db()

    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    logging.info("Bot rodando...")

    app.run_polling()

if __name__ == "__main__":
    main()
