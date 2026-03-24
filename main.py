import os
import json
import logging
import asyncio
import threading
import sqlite3
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from openai import AsyncOpenAI
from html import escape
import trafilatura
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# ================== ENV & CONFIG ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Inicializa cliente OpenAI
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

# ================== BANCO DE DADOS (CACHE) ==================
def init_db():
    conn = sqlite3.connect('cache_noticias.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS resumos (
            url TEXT PRIMARY KEY,
            titulo TEXT,
            resumo TEXT,
            fonte TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_cached_summary(url):
    conn = sqlite3.connect('cache_noticias.db')
    cursor = conn.cursor()
    cursor.execute('SELECT titulo, resumo, fonte FROM resumos WHERE url = ?', (url,))
    result = cursor.fetchone()
    conn.close()
    return result

def save_to_cache(url, titulo, resumo, fonte):
    conn = sqlite3.connect('cache_noticias.db')
    cursor = conn.cursor()
    try:
        cursor.execute('INSERT INTO resumos VALUES (?, ?, ?, ?)', (url, titulo, resumo, fonte))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

# ================== DUMMY SERVER ==================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Curador OpenAI Operacional")
    def log_message(self, format, *args): pass

def run_dummy_server():
    port = int(os.getenv("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), DummyHandler).serve_forever()

# ================== EXTRAÇÃO ==================
def bulletproof_scrape(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        extracted = trafilatura.extract(response.text, output_format='json', include_comments=False)
        return json.loads(extracted) if extracted else None
    except Exception as e:
        logging.error(f"Erro ao raspar: {e}")
        return None

# ================== HANDLERS ==================
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    url = update.message.text.strip()
    if not url.startswith("http"): return

    # 1. Verifica Cache para economizar API
    cached = get_cached_summary(url)
    if cached:
        titulo, resumo, fonte = cached
        final_post = (
            f"<b>{escape(titulo)}</b> (Cache 📦)\n\n"
            f"<blockquote><i>{escape(resumo)}</i></blockquote>\n\n"
            f'<i>Via: <a href="{url}">{escape(fonte)}</a></i>'
        )
        await update.message.reply_text(final_post, parse_mode=ParseMode.HTML)
        return

    msg = await update.message.reply_text("⚡ Lendo e processando...")

    try:
        data = await asyncio.to_thread(bulletproof_scrape, url)
        if not data or not data.get('text'):
            await msg.edit_text("❌ Não foi possível extrair o conteúdo.")
            return

        title = str(data.get('title') or 'Notícia')
        source = str(data.get('sitename') or 'Fonte')
        text = str(data.get('text') or '')[:4000] # Limite de entrada para economizar tokens

        # Prompt rico conforme solicitado
        prompt = (
            "Você é um curador de notícias profissional. Resuma a notícia abaixo em um parágrafo "
            "envolvente, direto e informativo para redes sociais. "
            "Regras: Use tom jornalístico, sem saudações, sem hashtags e sem formatação markdown (sem asteriscos)."
            f"\n\nTítulo: {title}\nConteúdo: {text}"
        )
        
        # Chamada OpenAI com gpt-4o-mini (custo-benefício máximo)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250, # Economia no output
            temperature=0.7
        )
        
        resumo_limpo = response.choices[0].message.content.strip().replace('**', '')

        # Salva no cache antes de enviar
        save_to_cache(url, title, resumo_limpo, source)

        final_post = (
            f"<b>{escape(title)}</b>\n\n"
            f"<blockquote><i>{escape(resumo_limpo)}</i></blockquote>\n\n"
            f'<i>Via: <a href="{url}">{escape(source)}</a></i>'
        )
        
        await msg.edit_text(final_post, parse_mode=ParseMode.HTML)

    except Exception as e:
        logging.error(f"Erro: {e}")
        await msg.edit_text(f"❌ Falha no processamento.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        # Mensagem personalizada conforme solicitado
        await update.message.reply_text("Olá! Envie o link de uma notícia e eu crio o post para você...")

# ================== MAIN ==================
def main():
    init_db() # Inicia o SQLite
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_url))
    
    print("Bot Curador OpenAI Iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
