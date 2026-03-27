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

# ================= NOVO: AUTORIZAÇÃO =================
AUTHORIZED_USERS = set()

def is_authorized(user):
    if user.id == ADMIN_ID:
        return True
    if user.username and user.username.lower() in AUTHORIZED_USERS:
        return True
    return False

# ================= COMANDO /convidado =================
async def convidado(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Apenas o administrador pode adicionar convidados.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Use: /convidado @usuario")
        return

    username = context.args[0].replace("@", "").lower()
    AUTHORIZED_USERS.add(username)

    await update.message.reply_text(f"✅ @{username} foi autorizado a usar o bot.")

# ================= COMANDO /revoke =================
async def revoke(update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Apenas o administrador pode remover convidados.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Use: /revoke @usuario")
        return

    username = context.args[0].replace("@", "").lower()

    if username in AUTHORIZED_USERS:
        AUTHORIZED_USERS.remove(username)
        await update.message.reply_text(f"❌ @{username} foi removido dos autorizados.")
    else:
        await update.message.reply_text("⚠️ Usuário não estava autorizado.")

# ================= TECLADOS INLINE =================
def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publicar", callback_data="publicar_sim")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="publicar_nao")]
    ])

# ================= SCRAPING =================
def scrape(url: str) -> str:
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        logging.warning(f"Requests falhou, tentando cloudscraper. Erro: {e}")

    try:
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        logging.error(f"Cloudscraper falhou. Erro: {e}")

    return ""

# ================= EXTRAÇÃO DE TEXTO =================
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

# ================= RESUMO COM GEMINI =================
def resumir(texto: str) -> str:
    if not texto or len(texto) < 150:
        return "Texto insuficiente para gerar resumo."

    texto_seguro = texto[:20000]

    prompt = f"""
    Você é um jornalista experiente. Crie um resumo direto e objetivo da notícia abaixo.
    
    Regras estritas:
    1. Vá direto ao ponto.
    2. Reescreva com suas próprias palavras.
    3. Máximo 400 caracteres.
    4. Tom neutro e informativo.

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
        logging.error(f"Erro na API do Gemini: {e}")
        return "Erro ao processar o resumo."

    return "Não foi possível gerar o resumo."

# ================= UTILIDADES =================
def get_fonte_nome(url: str) -> str:
    try:
        dominio = urlparse(url).netloc.replace("www.", "")
        nome = dominio.split(".")[0]
        return nome.capitalize()
    except:
        return "Web"

def formatar(titulo: str, resumo: str, fonte: str, link: str) -> str:
    return (
        f"<b>{titulo}</b>\n"
        f"<blockquote><i>{resumo}</i></blockquote>\n"
        f"<i>Via: {fonte}</i>"
        f'<a href="{link}">&#8203;</a>'
    )

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return
    
    context.user_data.clear()
    await update.message.reply_text("🤖 <b>pidroCURATOR ativo</b>", parse_mode=ParseMode.HTML)

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_authorized(update.effective_user):
        return

    texto_msg = update.message.text.strip() if update.message.text else ""

    if texto_msg.startswith("http"):
        msg = await update.message.reply_text("🔎 Processando...")

        html = scrape(texto_msg)
        titulo, texto_extraido = extrair(html)
        resumo = resumir(texto_extraido)
        fonte = get_fonte_nome(texto_msg)

        mensagem_final = formatar(titulo, resumo, fonte, texto_msg)

        context.user_data["mensagem"] = mensagem_final

        await msg.delete()
        await update.message.reply_text(mensagem_final, parse_mode=ParseMode.HTML)

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("convidado", convidado))
    app.add_handler(CommandHandler("revoke", revoke))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    logging.info("Bot iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()