import os
import logging
import time
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

# ================= CONFIG =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise ValueError("Variáveis de ambiente obrigatórias não definidas.")

ADMIN_ID = int(ADMIN_ID)

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}

# ================= FUNÇÕES AUXILIARES / KEYBOARD =================
def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publicar", callback_data="publicar_sim")],
        [
            InlineKeyboardButton("📝 Editar", callback_data="edit"),
            InlineKeyboardButton("❌ Cancelar", callback_data="publicar_nao")
        ]
    ])

# ================= SCRAPING =================
def scrape(url: str) -> str:
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        logging.warning(f"requests erro: {e}")

    try:
        scraper = cloudscraper.create_scraper()
        response = scraper.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        logging.error(f"cloudscraper erro: {e}")

    return ""

# ================= EXTRAÇÃO =================
def extrair(html: str):
    titulo = "Sem título"
    texto = ""

    try:
        downloaded = trafilatura.extract(html, include_comments=False)
        if downloaded and len(downloaded) > 200:
            texto = downloaded
    except Exception as e:
        logging.warning(f"trafilatura erro: {e}")

    if not texto:
        try:
            soup = BeautifulSoup(html, "html.parser")

            for tag in soup(["script", "style"]):
                tag.decompose()

            if soup.title and soup.title.string:
                titulo = soup.title.string.strip()

            paragraphs = soup.find_all("p")
            texto = " ".join(p.get_text(strip=True) for p in paragraphs)

        except Exception as e:
            logging.error(f"BeautifulSoup erro: {e}")

    try:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            titulo = meta.title.strip()
    except:
        pass

    return titulo, texto

# ================= RESUMO (CORRIGIDO) =================
def resumir(texto: str) -> str:
    if not texto or len(texto) < 200:
        return "Texto insuficiente para gerar resumo."

    texto = texto[:6000]

    def parece_copia(resumo, original):
        inicio = original[:500].lower()
        resumo_limpo = resumo.lower().strip()
        return resumo_limpo in inicio or inicio.startswith(resumo_limpo[:100])

    prompt = f"""
Resuma a notícia abaixo em português seguindo EXATAMENTE:

- Máximo de 3 frases
- Apenas 1 parágrafo
- Máximo de 300 caracteres
- Linguagem jornalística objetiva
- REESCREVA com suas próprias palavras
- NÃO copie frases do texto original
- NÃO comece igual ao texto original

Texto:
{texto}
"""

    for tentativa in range(3):
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt
            )

            resumo = ""

            if response:
                if hasattr(response, "text") and response.text:
                    resumo = response.text
                else:
                    try:
                        resumo = response.candidates[0].content.parts[0].text
                    except:
                        pass

            if resumo:
                resumo = resumo.strip()

                if parece_copia(resumo, texto):
                    logging.warning("Resumo rejeitado (cópia detectada), tentando novamente...")
                    continue

                resumo = re.sub(r'\s+', ' ', resumo)

                return resumo[:300]

        except Exception as e:
            logging.warning(f"Gemini erro (tentativa {tentativa+1}): {e}")
            time.sleep(1)

    try:
        frases = re.split(r'(?<=[.!?]) +', texto)
        frases = frases[:2]

        resumo = " ".join(frases).strip()

        resumo = resumo.replace("Segundo", "De acordo com")
        resumo = resumo.replace("De acordo com", "Conforme")

        return resumo[:300]

    except Exception as e:
        logging.error(f"Fallback erro: {e}")

    return "Não foi possível gerar o resumo."

# ================= UTIL =================
def get_fonte_nome(url: str) -> str:
    try:
        dominio = urlparse(url).netloc.replace("www.", "")
        nome = dominio.split(".")[0]
        return nome.capitalize()
    except:
        return "Fonte"

def formatar(titulo, resumo, fonte, link):
    return (
        f"<b>{titulo}</b>\n"
        f"<blockquote><i>{resumo}</i></blockquote>\n"
        f"<i>Fonte: {fonte}</i>"
        f'<a href="{link}">&#8203;</a>'
    )

# ================= TELEGRAM =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return

    await update.message.reply_text("📝 Envie um link de notícia.")

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_user.id != ADMIN_ID:
        return

    if update.message.text and context.user_data.get('is_editing'):
        context.user_data['mensagem'] = update.message.text_html
        context.user_data['is_editing'] = False
        
        await update.message.reply_text(
            "✅ <b>Texto atualizado!</b> Confira abaixo:",
            parse_mode=ParseMode.HTML
        )
        await update.message.reply_text(
            context.user_data['mensagem'],
            reply_markup=get_admin_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return

    if context.user_data.get("aguardando_id"):
        canal_id = update.message.text.strip()
        try:
            await context.bot.send_message(
                chat_id=canal_id,
                text=context.user_data["mensagem"],
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
            await update.message.reply_text("📢 Post enviado!")
        except Exception as e:
            logging.error(f"Erro ao enviar: {e}")
            await update.message.reply_text("❌ Erro ao publicar. Verifique o ID e as permissões do bot.")
        
        context.user_data.clear()
        return

    texto_msg = update.message.text.strip() if update.message.text else ""
    if texto_msg.startswith("http"):
        await update.message.reply_text("🔎 Processando...")
        try:
            html = scrape(texto_msg)
            if not html:
                await update.message.reply_text("Erro ao acessar o site.")
                return

            titulo, texto = extrair(html)
            if not texto:
                await update.message.reply_text("Erro ao extrair conteúdo.")
                return

            resumo = resumir(texto)
            fonte = get_fonte_nome(texto_msg)
            mensagem = formatar(titulo, resumo, fonte, texto_msg)

            context.user_data["mensagem"] = mensagem

            await update.message.reply_text(
                mensagem,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )

            await update.message.reply_text(
                "📣 O que deseja fazer com esta notícia?",
                reply_markup=get_admin_keyboard()
            )
        except Exception as e:
            logging.exception("Erro geral")
            await update.message.reply_text("Erro interno ao processar.")
    else:
        await update.message.reply_text("Envie um link válido ou use os botões.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.message.reply_text("⛔ Acesso não autorizado.")
        return

    if query.data == "publicar_sim":
        context.user_data["aguardando_id"] = True
        context.user_data['is_editing'] = False
        await query.message.reply_text("🔢 Qual o ID do canal (ex: @meucanal ou -100...)?")
    
    elif query.data == "edit":
        text_to_edit = context.user_data.get('mensagem', '')
        if text_to_edit:
            context.user_data['is_editing'] = True
            await query.message.reply_text(
                "👇 <b>Copie a mensagem abaixo, cole na sua caixa de texto, edite usando a própria formatação visual do Telegram e me envie de volta:</b>",
                parse_mode=ParseMode.HTML
            )
            await query.message.reply_text(
                text_to_edit,
                parse_mode=ParseMode.HTML
            )
    
    elif query.data == "publicar_nao":
        context.user_data.clear()
        await query.message.reply_text("❌ Cancelado.")

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_router))

    logging.info("Bot iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()