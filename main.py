import os
import logging
import time
import re
from urllib.parse import urlparse

import requests
import cloudscraper
import trafilatura
from bs4 import BeautifulSoup

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise ValueError("Variáveis de ambiente obrigatórias não definidas.")

ADMIN_ID = int(ADMIN_ID)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}


# ================= SCRAPING =================
def scrape(url: str) -> str:
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return response.text

        logging.warning(f"requests falhou: {response.status_code}")
    except Exception as e:
        logging.warning(f"requests erro: {e}")

    # fallback cloudscraper
    try:
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, headers=HEADERS, timeout=10)

        if response.status_code == 200:
            return response.text

    except Exception as e:
        logging.error(f"cloudscraper erro: {e}")

    return ""


# ================= EXTRAÇÃO =================
def extrair(html: str, url: str):
    titulo = "Sem título"
    texto = ""

    try:
        downloaded = trafilatura.extract(html, include_comments=False)

        if downloaded and len(downloaded) > 200:
            texto = downloaded

    except Exception as e:
        logging.warning(f"trafilatura erro: {e}")

    # fallback BeautifulSoup
    if not texto:
        try:
            soup = BeautifulSoup(html, "html.parser")

            for tag in soup(["script", "style"]):
                tag.decompose()

            # título
            if soup.title and soup.title.string:
                titulo = soup.title.string.strip()

            paragraphs = soup.find_all("p")
            texto = " ".join(p.get_text(strip=True) for p in paragraphs)

        except Exception as e:
            logging.error(f"BeautifulSoup erro: {e}")

    # tenta título via trafilatura metadata
    try:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            titulo = meta.title.strip()
    except:
        pass

    return titulo, texto


# ================= RESUMO =================
def resumir(texto: str) -> str:
    if not texto or len(texto) < 200:
        return "Texto insuficiente para gerar resumo."

    texto = texto[:6000]

    prompt = f"""
Resuma a seguinte notícia em português.

Regras:
- Um único parágrafo
- 4 a 6 frases
- Linguagem jornalística
- Comece direto pelo fato principal

Texto:
{texto}
"""

    for tentativa in range(2):
        try:
            from google import genai

            client = genai.Client(api_key=GEMINI_API_KEY)

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )

            if response and response.text:
                resumo = response.text.strip()

                if len(resumo) > 50:
                    return resumo

        except Exception as e:
            logging.warning(f"Gemini erro (tentativa {tentativa+1}): {e}")
            time.sleep(2)

    # fallback
    try:
        frases = re.split(r'(?<=[.!?]) +', texto)
        resumo = " ".join(frases[:5]).strip()
        if resumo:
            return resumo
    except Exception as e:
        logging.error(f"Fallback erro: {e}")

    return "Não foi possível gerar o resumo."


# ================= UTIL =================
def get_fonte(url: str) -> str:
    try:
        dominio = urlparse(url).netloc.replace("www.", "")
        return dominio
    except:
        return "Fonte desconhecida"


def formatar(titulo, resumo, fonte, link):
    return (
        f"<b>📰 {titulo}</b>\n\n"
        f"<blockquote><i>{resumo}</i></blockquote>\n\n"
        f"Fonte: <i>{fonte}</i>\n\n"
        f'<a href="{link}">🔗 Leia mais</a>'
    )


# ================= TELEGRAM =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return

    await update.message.reply_text("Envie um link de notícia.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return

    url = update.message.text.strip()

    if not url.startswith("http"):
        await update.message.reply_text("Envie um link válido.")
        return

    await update.message.reply_text("🔎 Processando...")

    try:
        html = scrape(url)

        if not html:
            await update.message.reply_text("Erro ao acessar o site.")
            return

        titulo, texto = extrair(html, url)

        if not texto:
            await update.message.reply_text("Erro ao extrair conteúdo.")
            return

        resumo = resumir(texto)
        fonte = get_fonte(url)

        mensagem = formatar(titulo, resumo, fonte, url)

        await update.message.reply_text(
            mensagem,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False
        )

    except Exception as e:
        logging.exception("Erro geral")
        await update.message.reply_text("Erro interno ao processar.")


# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logging.info("Bot iniciado...")
    app.run_polling()


if __name__ == "__main__":
    main()