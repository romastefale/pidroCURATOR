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
    # Botão de editar foi removido daqui
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publicar", callback_data="publicar_sim")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="publicar_nao")]
    ])

# ================= SCRAPING =================
def scrape(url: str) -> str:
    """Tenta baixar o HTML da página usando requests e cloudscraper como fallback."""
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
    """Extrai título e texto limpo do HTML."""
    titulo = "Sem título"
    texto = ""

    # Tentativa 1: Trafilatura (Excelente para artigos/notícias)
    try:
        texto_extraido = trafilatura.extract(html, include_comments=False)
        if texto_extraido and len(texto_extraido) > 200:
            texto = texto_extraido
            
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            titulo = meta.title.strip()
    except Exception as e:
        logging.warning(f"Erro no trafilatura: {e}")

    # Tentativa 2: Fallback para BeautifulSoup se o Trafilatura falhar
    if not texto:
        try:
            soup = BeautifulSoup(html, "html.parser")
            
            # Limpa tags indesejadas
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
    """Usa o Gemini para gerar um resumo limpo e direto da notícia."""
    if not texto or len(texto) < 150:
        return "Texto insuficiente para gerar resumo."

    # Limita o tamanho do texto para economizar tokens e focar no conteúdo principal
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
            return response.text.strip()
            
    except Exception as e:
        logging.error(f"Erro na API do Gemini: {e}")
        return "Erro ao processar o resumo com a Inteligência Artificial."

    return "Não foi possível gerar o resumo."

# ================= UTILIDADES =================
def get_fonte_nome(url: str) -> str:
    """Extrai o nome do domínio principal para usar como Fonte."""
    try:
        dominio = urlparse(url).netloc.replace("www.", "")
        nome = dominio.split(".")[0]
        return nome.capitalize()
    except:
        return "Web"

def formatar(titulo: str, resumo: str, fonte: str, link: str) -> str:
    """Monta a estrutura HTML da mensagem para o Telegram."""
    return (
        f"<b>{titulo}</b>\n"
        f"<blockquote><i>{resumo}</i></blockquote>\n"
        f"<i>Via: {fonte}</i>\n"
        f'<a href="{link}">&#8203;</a>' # Link invisível para gerar preview
    )

# ================= HANDLERS DO TELEGRAM =================
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

    # 1. FLUXO: Aguardando ID do canal para publicar
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
                "1. Se o ID está correto (ex: @meucanal ou -100123...)\n"
                "2. Se o bot é administrador do canal."
            )
        
        context.user_data.clear()
        return

    # 2. FLUXO: Recebendo uma nova URL
    if texto_msg.startswith("http"):
        context.user_data.clear() 
        msg_processamento = await update.message.reply_text("🔎 Baixando e analisando a notícia...")
        
        try:
            html = scrape(texto_msg)
            if not html:
                await msg_processamento.edit_text("❌ Erro: Não foi possível acessar o conteúdo deste site (bloqueio ou fora do ar).")
                return

            titulo, texto_extraido = extrair(html)
            if not texto_extraido:
                await msg_processamento.edit_text("❌ Erro: Não encontrei texto útil nesta página.")
                return

            await msg_processamento.edit_text("🧠 Gerando resumo com IA...")
            resumo = resumir(texto_extraido)
            fonte = get_fonte_nome(texto_msg)
            
            mensagem_final = formatar(titulo, resumo, fonte, texto_msg)
            
            # Salvando os dados na sessão
            context.user_data["mensagem"] = mensagem_final
            context.user_data["link_original"] = texto_msg 

            # Remove a mensagem de processamento para deixar o chat limpo
            await msg_processamento.delete()
            
            await update.message.reply_text(
                mensagem_final,
                parse_mode=ParseMode.HTML
            )

            await update.message.reply_text(
                "📣 O que deseja fazer com esta notícia?",
                reply_markup=get_admin_keyboard()
            )
            
        except Exception as e:
            logging.exception("Erro geral no processamento do link")
            await msg_processamento.edit_text("❌ Ocorreu um erro interno ao processar este link.")
    else:
        await update.message.reply_text("⚠️ Comando não reconhecido. Por favor, envie um link válido (começando com http/https).")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.message.reply_text("⛔ Acesso não autorizado.")
        return

    if query.data == "publicar_sim":
        context.user_data["aguardando_id"] = True
        await query.message.edit_text("🔢 Envie o <b>ID do canal</b> (ex: @meucanal ou -100...).\n\n<i>Ou digite 'cancelar' para abortar.</i>", parse_mode=ParseMode.HTML)
    
    elif query.data == "publicar_nao":
        context.user_data.clear()
        await query.message.edit_text("❌ Ação cancelada pelo usuário. Pode enviar o próximo link!")

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    logging.info("Bot iniciado com sucesso e aguardando links...")
    app.run_polling()

if __name__ == "__main__":
    main()
