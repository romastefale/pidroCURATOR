import os
import logging
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from google import genai
from html import escape
import trafilatura
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
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

# Inicialização Gemini
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ================== LOG ==================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# ================== DUMMY SERVER ==================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Curador Ativo")
    def log_message(self, format, *args): pass

def run_dummy_server():
    port = int(os.getenv("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), DummyHandler).serve_forever()

# ================== NÚCLEO DE PROCESSAMENTO ==================

def extract_article(url):
    """Extrai o texto real da notícia ignorando lixo do site."""
    downloaded = trafilatura.fetch_url(url)
    if downloaded:
        # Extrai texto, título e metadados
        result = trafilatura.extract(downloaded, include_comments=False, output_format='json')
        import json
        return json.loads(result) if result else None
    return None

def summarize_with_gemini(title, content):
    """Gera o resumo profissional no estilo pidroNEWS."""
    try:
        prompt = f"""
        Você é um editor de notícias profissional. 
        Reescreva o conteúdo abaixo para um post de Telegram, mantendo um tom sério e informativo.
        
        TÍTULO ORIGINAL: {title}
        CONTEÚDO: {content}

        REGRAS:
        1. Foque nos fatos principais.
        2. Use parágrafos curtos.
        3. Adicione contexto e impacto se possível.
        4. Responda APENAS com o corpo do resumo (sem repetir o título).
        """
        
        response = gemini_client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        logging.error(f"Erro Gemini: {e}")
        return "Erro ao gerar resumo automático."

# ================== HANDLERS ==================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Segurança: Apenas você pode usar o bot
    if update.effective_user.id != ADMIN_ID:
        return

    text = update.message.text
    if not text.startswith("http"):
        return

    processing_msg = await update.message.reply_text("Processando link e gerando resumo... ⏳")

    try:
        url = text.strip()
        article_data = extract_article(url)

        if not article_data or not article_data.get('text'):
            await processing_msg.edit_text("❌ Não consegui extrair o conteúdo desta URL.")
            return

        title = article_data.get('title', 'Notícia')
        content = article_data.get('text')[:4000] # Limite para não estourar o prompt
        source_name = article_data.get('sitename', 'Fonte')

        # Gera o resumo
        summary = summarize_with_gemini(title, content)

        # Montagem do Layout solicitado
        # Título em Negrito
        # Corpo em Citação + Itálico
        # Via: Link em Itálico
        final_post = (
            f"<b>{escape(title)}</b>\n\n"
            f"<blockquote><i>{escape(summary)}</i></blockquote>\n\n"
            f'<i>Via: <a href="{url}">{escape(source_name)}</a></i>'
        )

        await update.message.reply_text(final_post, parse_mode=ParseMode.HTML, disable_web_page_preview=False)
        await processing_msg.delete()

    except Exception as e:
        logging.error(f"Erro geral: {e}")
        await processing_msg.edit_text("❌ Ocorreu um erro ao processar essa notícia.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Olá, Piero! Envie o link de uma notícia e eu preparo o post para você.")

# ================== MAIN ==================

def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    # Monitora qualquer mensagem que pareça um link
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("Bot Curador Online...")
    app.run_polling()

if __name__ == "__main__":
    main()
