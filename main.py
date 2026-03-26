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

from google import genai

# ================= CONFIGURAÇÕES =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise ValueError("Variáveis de ambiente (TELEGRAM_TOKEN, GEMINI_API_KEY, ADMIN_ID) não definidas.")

ADMIN_ID = int(ADMIN_ID)

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}

# ================= FUNÇÃO NOVA (NORMALIZAÇÃO UTF-8) =================
def normalizar_texto(texto: str) -> str:
    """Garante que o texto esteja em UTF-8 corretamente (acentos, ç, etc)."""
    if not texto:
        return ""
    return texto.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")

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
            response.encoding = response.apparent_encoding
            return normalizar_texto(response.text)
    except Exception as e:
        logging.warning(f"Requests falhou, tentando cloudscraper. Erro: {e}")

    try:
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            response.encoding = response.apparent_encoding
            return normalizar_texto(response.text)
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
            texto = normalizar_texto(texto_extraido)
            
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            titulo = normalizar_texto(meta.title.strip())
    except Exception as e:
        logging.warning(f"Erro no trafilatura: {e}")

    if not texto:
        try:
            soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
            
            for tag in soup(["script", "style", "nav", "footer", "aside"]):
                tag.decompose()

            if soup.title and soup.title.string:
                titulo = normalizar_texto(soup.title.string.strip())

            paragraphs = soup.find_all("p")
            texto = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
            texto = re.sub(r'\s+', ' ', texto).strip()
            texto = normalizar_texto(texto)

        except Exception as e:
            logging.error(f"Erro no BeautifulSoup: {e}")

    return titulo, texto

# ================= RESUMO COM GEMINI =================
def resumir(texto: str) -> str:
    if not texto or len(texto) < 150:
        return "Texto insuficiente para gerar resumo."

    texto_seguro = texto[:15000]

    prompt = f"""
    Você é um jornalista experiente. Crie um resumo direto e objetivo da notícia abaixo.
    
    Regras estritas:
    1. Vá direto ao ponto. Não use frases como "A notícia fala sobre...".
    2. Reescreva com suas próprias palavras, sem copiar trechos exatos.
    3. O resumo deve ter NO MÁXIMO 300 caracteres.
    4. Mantenha um tom jornalístico, neutro e informativo.

    Texto da Notícia:
    {texto_seguro}
    """

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash", 
            contents=prompt
        )
        
        if response.text:
            return normalizar_texto(response.text.strip())
            
    except Exception as e:
        logging.error(f"Erro na API do Gemini: {e}")
        return "Erro ao processar o resumo com a Inteligência Artificial."

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
        f"<i>Via: {fonte}</i>\n"
        f'<a href="{link}">&#8203;</a>'
    )

def processar_hashtags(texto_entrada: str) -> str:
    texto_limpo = texto_entrada.replace(",", " ").replace(";", " ")
    palavras = texto_limpo.split()
    
    tags = []
    for palavra in palavras:
        if not palavra.startswith("#"):
            tags.append(f"#{palavra}")
        else:
            tags.append(palavra)
            
    return " ".join(tags)

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return
    
    context.user_data.clear()
    await update.message.reply_text("🤖 <b>Bot ativo!</b>\n📝 Envie o link de uma notícia para começarmos.", parse_mode=ParseMode.HTML)

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_user.id != ADMIN_ID:
        return

    texto_msg = update.message.text.strip() if update.message.text else ""

    if context.user_data.get("aguardando_hashtags"):
        if texto_msg.lower() == "cancelar":
            context.user_data.clear()
            await update.message.reply_text("❌ Publicação cancelada. Envie um novo link.")
            return
            
        if texto_msg.lower() != "pular":
            hashtags_formatadas = processar_hashtags(texto_msg)
            mensagem_antiga = context.user_data.get("mensagem", "")
            context.user_data["mensagem"] = f"{hashtags_formatadas}\n\n{mensagem_antiga}"
            
        context.user_data["aguardando_hashtags"] = False
        context.user_data["aguardando_id"] = True
        await update.message.reply_text("🔢 Agora envie o <b>ID do canal</b> (ex: @meucanal ou -100...).", parse_mode=ParseMode.HTML)
        return

    if context.user_data.get("aguardando_id"):
        if texto_msg.lower() == "cancelar":
            context.user_data.clear()
            await update.message.reply_text("❌ Publicação cancelada. Envie um novo link.")
            return

        canal_id = texto_msg
        try:
            await context.bot.send_message(
                chat_id=canal_id,
                text=context.user_data["mensagem"],
                parse_mode=ParseMode.HTML
            )
            await update.message.reply_text(f"📢 Post enviado com sucesso para {canal_id}!")
        except Exception as e:
            logging.error(f"Erro ao enviar para o canal: {e}")
            await update.message.reply_text(
                "❌ Erro ao publicar. Verifique:\n"
                "1. Se o ID está correto\n"
                "2. Se o bot é administrador."
            )
        
        context.user_data.clear()
        return
