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

# Apenas erros críticos no log para manter a performance de I/O alta
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
    """Finge ser um Chrome real para evitar bloqueios de firewall, depois extrai o texto."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    
    try:
        # 1. Faz o download mascarado
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html_content = response.text
        
        # 2. Extrai de forma limpa
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
    # Trava de segurança: apenas o ADMIN
    if update.effective_user.id != ADMIN_ID: return
    
    url = update.message.text.strip()
    if not url.startswith("http"): return

    # Feedback de início imediato
    msg = await update.message.reply_text("⚡ Lendo...")

    try:
        # Isola a requisição de rede em outra thread para não travar o bot
        data = await asyncio.to_thread(bulletproof_scrape, url)
        
        if not data or not data.get('text'):
            await msg.edit_text("❌ Não foi possível ler o site (bloqueio severo ou link inválido).")
            return

        title = data.get('title', 'Notícia')
        source = data.get('sitename') or 'Fonte'
        text = data.get('text', '')[:4500] # Limite seguro de contexto

        # Prompt blindado para evitar que a IA quebre o layout do Telegram
        prompt = (
            "Escreva um resumo direto e profissional desta notícia para um post. "
            "Regras estritas: Retorne APENAS o texto do resumo. Não use asteriscos, "
            "negritos ou marcações Markdown. Nada de saudações.\n\n"
            f"Título: {title}\nTexto: {text}"
        )
        
        # Chamada assíncrona nativa da IA (Latência mínima)
        response = await gemini_client.aio.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )
        
        resumo_limpo = response.text.strip().replace('**', '').replace('*', '')

        # Layout Inegociável (com blindagem de caracteres especiais via html.escape)
        final_post = (
            f"<b>{escape(title)}</b>\n\n"
            f"<blockquote><i>{escape(resumo_limpo)}</i></blockquote>\n\n"
            f'<i>Via: <a href="{url}">{escape(source)}</a></i>'
        )
        
        # Disable_web_page_preview=False garante que o Instant View / Preview da URL apareça
        await msg.edit_text(final_post, parse_mode=ParseMode.HTML, disable_web_page_preview=False)

    except Exception as e:
        logging.error(f"Erro Crítico: {e}")
        await msg.edit_text("❌ Ocorreu um erro no processamento interno.")

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
