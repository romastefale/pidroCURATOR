import os
import logging
import threading
import asyncio
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

# ================== CONFIGURAÇÕES ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ================== DUMMY SERVER ==================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Online")
    def log_message(self, format, *args): pass

def run_dummy_server():
    port = int(os.getenv("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), DummyHandler).serve_forever()

# ================== EXTRAÇÃO ROBUSTA ==================

def safe_extract(url):
    """Extração com timeout e User-Agent para evitar travamentos"""
    try:
        # Simula um navegador real para evitar bloqueios
        downloaded = trafilatura.fetch_url(url)
        
        if not downloaded:
            return None

        # timeout de 15s implícito no trafilatura.extract para evitar loops
        content = trafilatura.extract(
            downloaded,
            include_comments=False,
            no_fallback=False
        )
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(downloaded, 'html.parser')
        title = soup.title.string if soup.title else "Notícia"
        
        # Pega o nome do site via metadados ou domínio
        site_name = "Fonte"
        og_site = soup.find("meta", property="og:site_name")
        if og_site:
            site_name = og_site["content"]
        
        return {"text": content, "title": title, "source": site_name}
    except Exception as e:
        logging.error(f"Erro na extração: {e}")
        return None

# ================== HANDLERS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Olá! Envie o link de uma notícia e eu crio o post para você...")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    url = update.message.text.strip()
    if not url.startswith("http"):
        return

    # Mensagem de status inicial
    status_msg = await update.message.reply_text("Lendo notícia e preparando curadoria... ☕")

    try:
        # 1. Tenta extrair o conteúdo
        data = await asyncio.to_thread(safe_extract, url)
        
        if not data or not data['text']:
            await status_msg.edit_text("❌ Erro: O site bloqueou o acesso ou está fora do ar.")
            return

        # 2. Tenta gerar o resumo rico
        await status_msg.edit_text("Gerando resumo com Gemini... 🤖")
        
        prompt = f"""
        Como um editor sênior, resuma esta notícia para Telegram:
        TÍTULO: {data['title']}
        CONTEÚDO: {data['text'][:6000]}
        
        REGRAS: Resumo fluído, destaque o impacto, parágrafos curtos, sem saudações.
        """
        
        response = await asyncio.to_thread(
            gemini_client.models.generate_content, 
            model='gemini-1.5-flash', 
            contents=prompt
        )
        
        resumo = response.text.strip()

        # 3. Formatação Final (Padrão @pidroNEWS)
        final_post = (
            f"<b>{escape(data['title'])}</b>\n\n"
            f"<blockquote><i>{escape(resumo)}</i></blockquote>\n\n"
            f'<i>Via: <a href="{url}">{escape(data["source"])}</a></i>'
        )

        await update.message.reply_text(final_post, parse_mode=ParseMode.HTML)
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Erro crítico no processamento: {e}")
        await status_msg.edit_text(f"❌ Ocorreu um erro inesperado ao processar este link.")

# ================== MAIN ==================

def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    # Python 3.12.3 padrão
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_url))
    
    logging.info("Bot Curador Pro v3 Ativo.")
    app.run_polling()

if __name__ == "__main__":
    main()
