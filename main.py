import os
import json
import logging
import asyncio
import threading
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from openai import AsyncOpenAI  # Biblioteca para Grok
from html import escape
import trafilatura
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# ================== ENV ==================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
XAI_API_KEY = os.getenv("XAI_API_KEY") # Substitua a chave no seu ambiente

# Configuração Grok (xAI)
client = AsyncOpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1",
)

logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

# ================== DUMMY SERVER ==================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Curador Operacional")
    def log_message(self, format, *args): pass

def run_dummy_server():
    port = int(os.getenv("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), DummyHandler).serve_forever()

# ================== VALIDAÇÃO E EXTRAÇÃO ==================
def is_eligible(data):
    """
    Verifica se o link é elegível para resumo:
    - Deve ter um título.
    - Deve ter pelo menos 500 caracteres de texto útil.
    """
    if not data:
        return False
    text = data.get('text', '')
    return len(text) > 500

def bulletproof_scrape(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        extracted = trafilatura.extract(response.text, output_format='json')
        return json.loads(extracted) if extracted else None
    except Exception as e:
        logging.error(f"Erro ao raspar {url}: {e}")
        return None

# ================== HANDLERS ==================
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    url = update.message.text.strip()
    if not url.startswith("http"): return

    msg = await update.message.reply_text("⚡ Analisando elegibilidade...")

    try:
        data = await asyncio.to_thread(bulletproof_scrape, url)
        
        # Filtro de Economia: Só gasta API se o link for bom
        if not is_eligible(data):
            await msg.edit_text("⚠️ Link inelegível: O conteúdo é muito curto ou não foi possível extrair texto relevante.")
            return

        await msg.edit_text("🤖 Gerando resumo com Grok...")

        title = str(data.get('title') or 'Notícia')
        source = str(data.get('sitename') or 'Fonte')
        text = str(data.get('text') or '')[:4000] # Limite para economizar tokens de entrada

        # Prompt rico para o Grok
        response = await client.chat.completions.create(
            model="grok-beta", # Ou o modelo disponível na sua conta
            messages=[
                {"role": "system", "content": "Você é um curador de notícias experiente. Crie resumos executivos, profissionais e sem firulas."},
                {"role": "user", "content": f"Resuma a seguinte notícia de forma direta. Regras: Use tom jornalístico, ignore publicidade, retorne apenas o texto do resumo sem formatação Markdown ou asteriscos.\n\nTítulo: {title}\nConteúdo: {text}"}
            ],
            temperature=0.3, # Menor temperatura = mais direto e econômico
            max_tokens=300   # Limite de saída para controle de custos
        )
        
        resumo_limpo = response.choices[0].message.content.strip()

        final_post = (
            f"<b>{escape(title)}</b>\n\n"
            f"<blockquote><i>{escape(resumo_limpo)}</i></blockquote>\n\n"
            f'<i>Via: <a href="{url}">{escape(source)}</a></i>'
        )
        
        await msg.edit_text(final_post, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    except Exception as e:
        logging.error(f"Erro Grok: {e}")
        await msg.edit_text(f"❌ Falha no processamento: link complexo ou erro na API.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Olá! Envie o link de uma notícia e eu crio o post para você...")

# ================== MAIN ==================
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_url))
    
    print("Bot Curador com Grok iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
