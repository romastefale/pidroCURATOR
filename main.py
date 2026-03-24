import os
import json
import logging
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from html import escape
import trafilatura
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# ================== ENV ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# Nível WARNING deixa o bot mais rápido, pois para de "escrever" logs inúteis no terminal
logging.basicConfig(level=logging.WARNING)

# ================== DUMMY SERVER ==================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args): pass

def run_dummy_server():
    HTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), DummyHandler).serve_forever()

# ================== SCRAPER ULTRA RÁPIDO ==================
def fast_scrape(url):
    downloaded = trafilatura.fetch_url(url)
    if not downloaded: 
        return None
    # Extrai direto para JSON, pegando texto, título e nome do site de uma vez
    result = trafilatura.extract(downloaded, output_format='json', include_comments=False)
    return json.loads(result) if result else None

# ================== HANDLERS ==================
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    url = update.message.text.strip()
    if not url.startswith("http"): return

    # Apenas um aviso visual rápido
    msg = await update.message.reply_text("⚡ Lendo...")

    try:
        # 1. Extração isolada
        data = await asyncio.to_thread(fast_scrape, url)
        if not data or not data.get('text'):
            await msg.edit_text("❌ Erro ao ler o site.")
            return

        title = data.get('title', 'Notícia')
        source = data.get('sitename') or 'Fonte'
        text = data.get('text', '')[:4000] # Limite seguro e rápido para o Gemini

        # 2. Chamada IA NATIVA Assíncrona (Muito mais rápida)
        prompt = f"Resuma direto ao ponto para Telegram. Seja profissional. Sem saudações.\nTítulo: {title}\nTexto: {text}"
        
        response = await gemini_client.aio.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )

        # 3. Layout exato - Edita a mensagem original (poupa chamadas de rede)
        final_post = (
            f"<b>{escape(title)}</b>\n\n"
            f"<blockquote><i>{escape(response.text.strip())}</i></blockquote>\n\n"
            f'<i>Via: <a href="{url}">{escape(source)}</a></i>'
        )
        
        await msg.edit_text(final_post, parse_mode=ParseMode.HTML)

    except Exception as e:
        logging.error(f"Erro: {e}")
        await msg.edit_text("❌ Falha no processamento.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Mande o link.")

# ================== MAIN ==================
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_url))
    
    app.run_polling()

if __name__ == "__main__":
    main()
