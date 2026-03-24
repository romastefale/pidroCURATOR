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

# ================== DUMMY SERVER (RAILWAY) ==================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Curador Ativo")
    def log_message(self, format, *args): pass

def run_dummy_server():
    port = int(os.getenv("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), DummyHandler).serve_forever()

# ================== EXTRAÇÃO E IA ==================

def extract_content(url):
    # Trafilatura com tratamento de erro e User-Agent embutido
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None

    result = trafilatura.extract(
        downloaded, 
        include_comments=False,
        include_tables=False,
        no_fallback=False
    )
    
    # Extração de Título via metadados básica
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(downloaded, 'html.parser')
    title = soup.title.string if soup.title else "Notícia"
    
    return {"text": result, "title": title}

def summarize_rich(title, content):
    """Prompt rico para gerar curadoria de alta qualidade"""
    try:
        prompt = f"""
        Você é um editor sênior de um canal de notícias influente no Telegram.
        Sua tarefa é transformar a notícia abaixo em um post de curadoria impecável.

        TÍTULO ORIGINAL: {title}
        CONTEÚDO BRUTO: {content[:5000]}

        REGRAS DE OURO:
        1. Escreva um resumo fluído, profissional e direto.
        2. Destaque o IMPACTO da notícia e o CONTEXTO (por que isso importa agora?).
        3. Use parágrafos curtos para leitura rápida no celular.
        4. Não use saudações, responda apenas com o corpo do resumo.
        """
        
        response = gemini_client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        logging.error(f"Erro Gemini: {e}")
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

    status_msg = await update.message.reply_text("Lendo notícia e preparando curadoria... ☕")

    data = extract_content(url)
    
    if not data or not data['text']:
        await status_msg.edit_text("❌ Não foi possível extrair o texto. O site pode estar protegido.")
        return

    resumo = summarize_rich(data['title'], data['text'])
    
    if not resumo:
        await status_msg.edit_text("❌ Falha na comunicação com a IA.")
        return

    # Formatação solicitada para o @pidroNEWS
    final_post = (
        f"<b>{escape(data['title'])}</b>\n\n"
        f"<blockquote><i>{escape(resumo)}</i></blockquote>\n\n"
        f'<i>Via: <a href="{url}">Acesse a fonte</a></i>'
    )

    await update.message.reply_text(final_post, parse_mode=ParseMode.HTML)
    await status_msg.delete()

# ================== MAIN ==================

def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_url))
    
    print("Bot Curador v2 (Python 3.12.3) iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
