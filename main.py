import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from html import escape
import trafilatura
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# ================== ENV ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

logging.basicConfig(level=logging.INFO)

# ================== DUMMY SERVER ==================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Ativo")
    def log_message(self, format, *args): pass

def run_dummy_server():
    port = int(os.getenv("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), DummyHandler).serve_forever()

# ================== EXTRAÇÃO MELHORADA ==================

def extract_content(url):
    # Configura um User-Agent para não ser bloqueado por sites como MacMagazine
    downloaded = trafilatura.fetch_url(url)
    
    if not downloaded:
        return None

    # Tenta extrair o conteúdo principal
    result = trafilatura.extract(
        downloaded, 
        include_comments=False,
        no_fallback=False,
        include_tables=False
    )
    
    # Busca o título separadamente se falhar no extract
    import metadata_parser
    title = ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(downloaded, 'html.parser')
        title = soup.title.string if soup.title else "Notícia"
    except:
        title = "Notícia"

    return {"text": result, "title": title}

def summarize(title, content):
    try:
        prompt = f"Resuma de forma profissional e direta para um canal de notícias no Telegram:\n\nTítulo: {title}\nConteúdo: {content}"
        response = gemini_client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        logging.error(f"Erro Gemini: {e}")
        return None

# ================== HANDLERS ==================

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    url = update.message.text.strip()
    msg = await update.message.reply_text("Obtendo conteúdo da Apple... 🍎")

    data = extract_content(url)
    
    if not data or not data['text']:
        await msg.edit_text("❌ Não consegui ler o conteúdo desse link. O site pode estar bloqueando robôs.")
        return

    resumo = summarize(data['title'], data['text'][:5000])
    
    if not resumo:
        await msg.edit_text("❌ Erro ao gerar o resumo com IA.")
        return

    # Formatação exata solicitada
    final_text = (
        f"<b>{escape(data['title'])}</b>\n\n"
        f"<blockquote><i>{escape(resumo)}</i></blockquote>\n\n"
        f'<i>Via: <a href="{url}">MacMagazine</a></i>'
    )

    await update.message.reply_text(final_text, parse_mode=ParseMode.HTML)
    await msg.delete()

# ================== MAIN ==================

def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_url))
    app.run_polling()

if __name__ == "__main__":
    main()
