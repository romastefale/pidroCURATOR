import os
import json
import logging
import asyncio
import threading
import requests
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

logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

# ================== DUMMY SERVER (ANTI-SLEEP) ==================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Curador Operacional")
    def log_message(self, format, *args): pass

def run_dummy_server():
    port = int(os.getenv("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), DummyHandler).serve_forever()

# ================== EXTRAÇÃO ANTI-BLOQUEIO ==================
def bulletproof_scrape(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html_content = response.text
        
        extracted = trafilatura.extract(
            html_content, 
            output_format='json', 
            include_comments=False,
            include_tables=False
        )
        
        return json.loads(extracted) if extracted else None
    except Exception as e:
        logging.error(f"Falha ao raspar {url}: {e}")
        return None

# ================== HANDLERS ==================
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    url = update.message.text.strip()
    if not url.startswith("http"): return

    msg = await update.message.reply_text("⚡ Lendo...")

    try:
        data = await asyncio.to_thread(bulletproof_scrape, url)
        
        if not data or not data.get('text'):
            await msg.edit_text("❌ Não foi possível ler o site (bloqueio severo ou link inválido).")
            return

        # CORREÇÃO 1: Garantia absoluta de que será uma string, evitando crash no html.escape
        title = str(data.get('title') or 'Notícia')
        source = str(data.get('sitename') or 'Fonte')
        text = str(data.get('text') or '')[:4500] 

        prompt = (
            "Escreva um resumo direto e profissional desta notícia para um post. "
            "Regras estritas: Retorne APENAS o texto do resumo. Não use asteriscos, "
            "negritos ou marcações Markdown. Nada de saudações.\n\n"
            f"Título: {title}\nTexto: {text}"
        )
        
        response = await gemini_client.aio.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )
        
        resumo_limpo = response.text.strip().replace('**', '').replace('*', '')

        final_post = (
            f"<b>{escape(title)}</b>\n\n"
            f"<blockquote><i>{escape(resumo_limpo)}</i></blockquote>\n\n"
            f'<i>Via: <a href="{url}">{escape(source)}</a></i>'
        )
        
        # CORREÇÃO 2: Removido o argumento obsoleto 'disable_web_page_preview'. 
        # A API v21 do Telegram já processa o preview nativamente se a URL estiver no texto.
        await msg.edit_text(final_post, parse_mode=ParseMode.HTML)

    except Exception as e:
        logging.error(f"Erro Crítico: {e}")
        # CORREÇÃO 3: Agora o erro real será exibido para você caso a IA caia, evitando "ficar cego"
        await msg.edit_text(f"❌ Falha: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Bot ativo. Envie um link.")

# ================== MAIN ==================
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_url))
    
    app.run_polling()

if __name__ == "__main__":
    main()
