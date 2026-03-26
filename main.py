import os
import logging
import re
from urllib.parse import urlparse

import requests
import cloudscraper
import trafilatura
from bs4 import BeautifulSoup

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)

# Utilizando o novo SDK oficial do Google
from google import genai

# ================= CONFIGURAÇÕES =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise ValueError("Variáveis de ambiente (TELEGRAM_TOKEN, GEMINI_API_KEY, ADMIN_ID) não definidas.")

ADMIN_ID = int(ADMIN_ID)

# Inicializa o cliente do Gemini
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}

# ================= TECLADOS INLINE =================
def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publicar", callback_data="publicar_sim")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="publicar_nao")]
    ])

def get_hashtags_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ Pular", callback_data="pular_hashtags")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_acao")]
    ])

# ================= SCRAPING =================
def scrape(url: str) -> str:
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            response.encoding = response.apparent_encoding  # ✅ CORREÇÃO
            return response.text
    except Exception as e:
        logging.warning(f"Requests falhou, tentando cloudscraper. Erro: {e}")

    try:
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            response.encoding = response.apparent_encoding  # ✅ CORREÇÃO
            return response.text
    except Exception as e:
        logging.error(f"Cloudscraper falhou. Erro: {e}")

    return ""

# ================= EXTRAÇÃO =================
def extrair(html: str):
    titulo = "Sem título"
    texto = ""

    try:
        texto_extraido = trafilatura.extract(html, include_comments=False)
        if texto_extraido and len(texto_extraido) > 200:
            texto = texto_extraido
            
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            titulo = meta.title.strip()
    except Exception as e:
        logging.warning(f"Erro no trafilatura: {e}")

    if not texto:
        try:
            soup = BeautifulSoup(html, "html.parser")
            
            for tag in soup(["script", "style", "nav", "footer", "aside"]):
                tag.decompose()

            if soup.title and soup.title.string:
                titulo = soup.title.string.strip()

            paragraphs = soup.find_all("p")
            texto = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
            texto = re.sub(r'\s+', ' ', texto).strip()

        except Exception as e:
            logging.error(f"Erro no BeautifulSoup: {e}")

    return titulo, texto

# ================= RESUMO =================
def resumir(texto: str) -> str:
    if not texto or len(texto) < 150:
        return "Texto insuficiente para gerar resumo."

    texto_seguro = texto[:15000]

    prompt = f"""
    Você é um jornalista experiente. Crie um resumo direto e objetivo da notícia abaixo.
    Máximo 300 caracteres.

    Texto:
    {texto_seguro}
    """

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        if response.text:
            return response.text.strip()
    except Exception as e:
        logging.error(f"Erro Gemini: {e}")

    return "Erro ao gerar resumo."

# ================= UTIL =================
def get_fonte_nome(url: str) -> str:
    try:
        dominio = urlparse(url).netloc.replace("www.", "")
        return dominio.split(".")[0].capitalize()
    except:
        return "Web"

def formatar(titulo, resumo, fonte, link):
    return (
        f"<b>{titulo}</b>\n"
        f"<blockquote><i>{resumo}</i></blockquote>\n"
        f"<i>Via: {fonte}</i>\n"
        f'<a href="{link}">&#8203;</a>'
    )

def processar_hashtags(texto):
    palavras = texto.replace(",", " ").split()
    return " ".join([p if p.startswith("#") else f"#{p}" for p in palavras])

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Acesso não autorizado.")

    context.user_data.clear()
    await update.message.reply_text("Envie um link.")

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_user.id != ADMIN_ID:
        return

    texto_msg = update.message.text.strip()

    if texto_msg.startswith("http"):
        msg = await update.message.reply_text("Processando...")

        html = scrape(texto_msg)
        if not html:
            return await msg.edit_text("Erro ao acessar.")

        titulo, texto = extrair(html)
        resumo = resumir(texto)
        fonte = get_fonte_nome(texto_msg)

        final = formatar(titulo, resumo, fonte, texto_msg)

        await msg.delete()
        await update.message.reply_text(final, parse_mode=ParseMode.HTML)

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    logging.info("Rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
