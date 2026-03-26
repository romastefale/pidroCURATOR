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

    texto_seguro = texto[:15000]

    prompt = f"""
    Você é um jornalista experiente. Crie um resumo direto e objetivo da notícia abaixo.
    
    Regras estritas:
    1. Vá direto ao ponto. Não use frases como "A notícia fala sobre...".
    2. Reescreva com suas próprias palavras, sem copiar trechos exatos.
    3. O resumo deve ter NO MÁXIMO 300 caracteres.
    4. Mantenha um tom jornalístico, neutro e informativo.
    5. Remova o nome da fonte do título da notícia ou referências como "| fonte".

    Texto da Notícia:
    {texto_seguro}
    """

    try:
        # CORREÇÃO: "gemini-2.5-flash" não existe (ainda). Use "gemini-2.0-flash" ou "gemini-1.5-flash"
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash", 
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

    if context.user_data.get("aguardando_hashtags"):
        if texto_msg.lower() == "cancelar":
            context.user_data.clear()
            await update.message.reply_text("❌ Publicação cancelada. Envie um novo link.")
            return
            
        hashtags_formatadas = processar_hashtags(texto_msg)
        mensagem_antiga = context.user_data["mensagem"]
        context.user_data["mensagem"] = f"{hashtags_formatadas}\n\n{mensagem_antiga}"
            
        context.user_data["aguardando_hashtags"] = False
        context.user_data["aguardando_id"] = True
        await update.message.reply_text("🔢 Agora envie o <b>ID do canal</b> (ex: @meucanal).\n\n<i>Ou 'cancelar'.</i>", parse_mode=ParseMode.HTML)
        return

    if context.user_data.get("aguardando_id"):
        if texto_msg.lower() == "cancelar":
            context.user_data.clear()
            await update.message.reply_text("❌ Publicação cancelada.")
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
            await update.message.reply_text("❌ Erro ao publicar. Verifique o ID e se o bot é admin.")
        
        context.user_data.clear()
        return

    if texto_msg.startswith("http"):
        context.user_data.clear() 
        msg_processamento = await update.message.reply_text("🔎 Baixando e analisando a notícia...")
        
        try:
            html = scrape(texto_msg)
            if not html:
                await msg_processamento.edit_text("❌ Erro: Não foi possível acessar o conteúdo.")
                return

            titulo, texto_extraido = extrair(html)
            if not texto_extraido:
                await msg_processamento.edit_text("❌ Erro: Não encontrei texto útil.")
                return

            await msg_processamento.edit_text("🧠 Gerando resumo com IA...")
            resumo = resumir(texto_extraido)
            fonte = get_fonte_nome(texto_msg)
            
            mensagem_final = formatar(titulo, resumo, fonte, texto_msg)
            context.user_data["mensagem"] = mensagem_final
            context.user_data["link_original"] = texto_msg 

            await msg_processamento.delete()
            await update.message.reply_text(mensagem_final, parse_mode=ParseMode.HTML)
            await update.message.reply_text("📣 O que deseja fazer?", reply_markup=get_admin_keyboard())
            
        except Exception as e:
            logging.exception("Erro geral")
            await msg_processamento.edit_text("❌ Ocorreu um erro interno.")
    else:
        await update.message.reply_text("⚠️ Envie um link válido.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    # CORREÇÃO: Adicionados os casos para "pular_tags" e "cancelar_tags"
    if query.data == "publicar_sim":
        context.user_data["aguardando_hashtags"] = True
        botoes = [[
            InlineKeyboardButton("⏩ Pular", callback_data="pular_tags"),
            InlineKeyboardButton("✖️ Cancelar", callback_data="cancelar_tags")
        ]]
        await query.message.edit_text(
            "#️⃣ Envie as **hashtags** no chat ou escolha uma opção:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(botoes)
        )
    
    elif query.data == "pular_tags":
        context.user_data["aguardando_hashtags"] = False
        context.user_data["aguardando_id"] = True
        await query.message.edit_text("🔢 Envie o <b>ID do canal</b>:", parse_mode=ParseMode.HTML)

    elif query.data == "cancelar_tags" or query.data == "publicar_nao":
        context.user_data.clear()
        await query.message.edit_text("❌ Ação cancelada.")

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))
    logging.info("Bot iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
