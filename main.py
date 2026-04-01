import asyncio
import logging
import os
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import cloudscraper
import requests
import trafilatura
from bs4 import BeautifulSoup
from gnews import GNews
from googlenewsdecoder import gnewsdecoder
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
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

JOBQUEUE_NAME = "pidrocurator_jobqueue"
DEFAULT_POST_INTERVAL = 30
DEFAULT_POST_TARGET = None

# ================= TECLADOS INLINE =================
def get_admin_keyboard():
    # Botão de editar foi removido daqui
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publicar", callback_data="publicar_sim")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="publicar_nao")]
    ])


def _get_job_settings(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    return context.application.bot_data.setdefault(
        "jobqueue_settings",
        {
            "interval": DEFAULT_POST_INTERVAL,
            "target": DEFAULT_POST_TARGET,
            "active": False,
            "last_url": None,
        },
    )


def _get_job_settings_from_app(application) -> Dict[str, Any]:
    return application.bot_data.setdefault(
        "jobqueue_settings",
        {
            "interval": DEFAULT_POST_INTERVAL,
            "target": DEFAULT_POST_TARGET,
            "active": False,
            "last_url": None,
        },
    )


def _clear_jobqueue_jobs(application) -> None:
    job_queue = application.job_queue
    if not job_queue:
        return

    for job in job_queue.get_jobs_by_name(JOBQUEUE_NAME):
        job.schedule_removal()


def _render_jobqueue_menu(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    settings = _get_job_settings(context)
    status = "🟢 ATIVO" if settings["active"] else "🔴 INATIVO"
    target = settings["target"] or "não definido"
    interval = settings["interval"]

    texto = (
        "<b>⚙️ Automação de notícias</b>\n\n"
        f"<b>Status:</b> {status}\n"
        f"<b>Intervalo:</b> {interval} minuto(s)\n"
        f"<b>Destino:</b> {target}\n\n"
        "Configure os parâmetros abaixo e depois ative a automação."
    )

    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ Definir Intervalo", callback_data="jobqueue_set_interval")],
        [InlineKeyboardButton("🎯 Definir Destino", callback_data="jobqueue_set_target")],
        [
            InlineKeyboardButton("▶️ Ativar", callback_data="jobqueue_activate"),
            InlineKeyboardButton("⏸ Parar", callback_data="jobqueue_stop"),
        ],
        [InlineKeyboardButton("🔄 Atualizar", callback_data="jobqueue_menu")],
    ])

    return texto, teclado


def _parse_channel_target(texto: str):
    texto = texto.strip()
    if not texto:
        return None

    if texto.startswith("@"):
        return texto

    if re.fullmatch(r"-?\d+", texto):
        return int(texto)

    return texto


def _decode_google_news_url(url: str) -> Optional[str]:
    url = (url or "").strip()
    if not url:
        return None

    try:
        parsed = urlparse(url)
        if "news.google.com" not in parsed.netloc and "google.com" not in parsed.netloc:
            return url

        decoded = gnewsdecoder(url, interval=1)
        if isinstance(decoded, dict):
            if decoded.get("status") and decoded.get("decoded_url"):
                return decoded["decoded_url"].strip()
            logging.info("Não foi possível decodificar URL do Google News: %s", decoded.get("message"))
    except Exception as e:
        logging.warning(f"Falha ao decodificar URL do Google News: {e}")

    try:
        response = requests.head(url, headers=HEADERS, timeout=15, allow_redirects=True)
        final_url = response.url.strip()
        if final_url:
            return final_url
    except Exception as e:
        logging.warning(f"HEAD com redirecionamento falhou: {e}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        final_url = response.url.strip()
        if final_url:
            return final_url
    except Exception as e:
        logging.warning(f"GET com redirecionamento falhou: {e}")

    return None


def _obter_client_gnews() -> GNews:
    google_news = GNews(
        language="pt",
        country="BR",
        period="1d",
        max_results=20,
    )
    return google_news


def _coletar_noticias_gnews() -> List[Dict[str, Any]]:
    google_news = _obter_client_gnews()
    candidatos: List[Dict[str, Any]] = []

    fontes = [
        ("top", google_news.get_top_news),
        ("tech", lambda: google_news.get_news_by_topic("TECHNOLOGY")),
        ("geral", lambda: google_news.get_news_by_topic("WORLD")),
    ]

    for origem, getter in fontes:
        try:
            itens = getter() or []
            for item in itens:
                if isinstance(item, dict):
                    item = dict(item)
                    item["_origem"] = origem
                    candidatos.append(item)
        except Exception as e:
            logging.warning(f"Falha ao buscar notícias do GNews ({origem}): {e}")

    vistos = set()
    unicos: List[Dict[str, Any]] = []
    for item in candidatos:
        chave = (
            (item.get("url") or "").strip(),
            (item.get("title") or "").strip().lower(),
        )
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(item)

    return unicos


def _noticia_aprovada_pelo_gemini(titulo: str, texto: str, fonte: str, link: str) -> bool:
    trecho = (texto or "")[:8000]
    prompt = f"""
    Você é um editor jornalístico rigoroso.

    Analise a notícia abaixo e responda APENAS com uma palavra:
    - APROVAR
    - REPROVAR

    Reprovar se houver violência explícita, gore, conteúdo gráfico sensível, temas hediondos, crueldade extrema, apologia a crimes violentos ou descrição detalhada de ferimentos/mortes.
    Se o conteúdo for apenas informativo e sem esse tipo de material, aprovar.

    Título: {titulo}
    Fonte: {fonte}
    Link: {link}

    Texto:
    {trecho}
    """

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        resposta = (response.text or "").strip().upper()
        return "REPROVAR" not in resposta
    except Exception as e:
        logging.warning(f"Falha na checagem de segurança do Gemini: {e}")
        return True


async def _executar_job_postagem(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _get_job_settings(context)
    if not settings["active"] or not settings["target"]:
        return

    lock = context.application.bot_data.setdefault("auto_job_lock", asyncio.Lock())
    async with lock:
        settings = _get_job_settings(context)
        if not settings["active"] or not settings["target"]:
            return

        candidatos = _coletar_noticias_gnews()
        if not candidatos:
            logging.info("Job automático: nenhum candidato encontrado no GNews.")
            return

        for item in candidatos:
            link_bruto = item.get("url") or ""
            titulo_fonte = (item.get("title") or "Sem título").strip()

            link_real = _decode_google_news_url(link_bruto)
            if not link_real:
                logging.info("Job automático: link não pôde ser decodificado, ignorando.")
                continue

            html = scrape(link_real)
            if not html:
                logging.info("Job automático: falha ao baixar HTML em %s", link_real)
                continue

            titulo, texto_extraido = extrair(html)
            if not texto_extraido:
                logging.info("Job automático: texto insuficiente em %s", link_real)
                continue

            fonte = get_fonte_nome(link_real)

            if not _noticia_aprovada_pelo_gemini(titulo or titulo_fonte, texto_extraido, fonte, link_real):
                logging.info("Job automático: notícia rejeitada pelo filtro de segurança.")
                continue

            resumo = resumir(texto_extraido)
            mensagem_final = formatar(titulo, resumo, fonte, link_real)

            try:
                await context.bot.send_message(
                    chat_id=settings["target"],
                    text=mensagem_final,
                    parse_mode=ParseMode.HTML
                )
                settings["last_url"] = link_real
                logging.info("Job automático: notícia publicada com sucesso em %s", settings["target"])
                return
            except Exception as e:
                logging.error(f"Erro ao publicar notícia automática: {e}")
                return

        logging.info("Job automático: nenhuma notícia passou no filtro ou pôde ser publicada.")


def _agendar_jobqueue(application) -> bool:
    settings = _get_job_settings_from_app(application)
    if not settings["active"] or not settings["target"]:
        return False

    job_queue = application.job_queue
    if not job_queue:
        raise RuntimeError("JobQueue não está disponível. Instale python-telegram-bot[job-queue].")

    _clear_jobqueue_jobs(application)
    job_queue.run_repeating(
        _executar_job_postagem,
        interval=timedelta(minutes=int(settings["interval"])),
        first=10,
        name=JOBQUEUE_NAME,
    )
    return True

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
    texto_seguro = texto[:20000]

    prompt = f"""
    Você é um jornalista experiente. Crie um resumo direto e objetivo da notícia abaixo.

    Regras estritas:
    1. Vá direto ao ponto. Não use frases como "A notícia fala sobre...".
    2. Reescreva com suas próprias palavras, sem copiar trechos exatos.
    3. Não omita informações, seja conciso e de fácil entendimento.
    3. O resumo deve ter NO MÁXIMO 280 caracteres.
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
        f"<i>Via: {fonte}</i>"
        f'<a href="{link}">&#8203;</a>' # Link invisível para gerar preview
    )


async def _mostrar_menu_jobqueue(update: Update, context: ContextTypes.DEFAULT_TYPE, *, editar: bool = False) -> None:
    texto, teclado = _render_jobqueue_menu(context)
    if editar and update.callback_query:
        await update.callback_query.message.edit_text(texto, reply_markup=teclado, parse_mode=ParseMode.HTML)
        return

    if update.message:
        await update.message.reply_text(texto, reply_markup=teclado, parse_mode=ParseMode.HTML)


async def jobqueue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return

    await _mostrar_menu_jobqueue(update, context)

# ================= HANDLERS DO TELEGRAM =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return

    context.user_data.clear()
    await update.message.reply_text("⚠️🤖 <b>pidroCURATOR está ativo!</b>\n\n<i>📲📝Envie o link de uma notícia para começarmos.</i>", parse_mode=ParseMode.HTML)

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.effective_user.id != ADMIN_ID:
        return

    texto_msg = update.message.text.strip() if update.message.text else ""

    # 0. FLUXO: Aguardando configuração do JobQueue
    if context.user_data.get("aguardando_jobqueue_interval"):
        if texto_msg.lower() == "cancelar":
            context.user_data.pop("aguardando_jobqueue_interval", None)
            await update.message.reply_text("❌ Configuração cancelada. Abra /jobqueue novamente.")
            return

        try:
            intervalo = int(texto_msg)
            if intervalo <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ Envie um número inteiro válido de minutos. Ex: 15")
            return

        settings = _get_job_settings(context)
        settings["interval"] = intervalo
        context.user_data.pop("aguardando_jobqueue_interval", None)

        await update.message.reply_text(f"✅ Intervalo atualizado para {intervalo} minuto(s).")
        await _mostrar_menu_jobqueue(update, context)
        return

    if context.user_data.get("aguardando_jobqueue_target"):
        if texto_msg.lower() == "cancelar":
            context.user_data.pop("aguardando_jobqueue_target", None)
            await update.message.reply_text("❌ Configuração cancelada. Abra /jobqueue novamente.")
            return

        destino = _parse_channel_target(texto_msg)
        if destino is None:
            await update.message.reply_text("⚠️ Envie um @canal ou um ID numérico válido. Ex: @meucanal ou -1001234567890")
            return

        settings = _get_job_settings(context)
        settings["target"] = destino
        context.user_data.pop("aguardando_jobqueue_target", None)

        await update.message.reply_text(f"✅ Destino atualizado para {destino}.")
        await _mostrar_menu_jobqueue(update, context)
        return

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
        await query.message.edit_text("🔢 Envie o <b>ID do canal</b> <i>(ex: @meucanal ou -100...).</i>", parse_mode=ParseMode.HTML)

    elif query.data == "publicar_nao":
        context.user_data.clear()
        await query.message.edit_text("❌ Ação cancelada pelo usuário. Pode enviar o próximo link!")

    elif query.data == "jobqueue_menu":
        await _mostrar_menu_jobqueue(update, context, editar=True)

    elif query.data == "jobqueue_set_interval":
        context.user_data["aguardando_jobqueue_interval"] = True
        await query.message.edit_text(
            "⏱ Envie o <b>intervalo em minutos</b> para a automação.\n\nDigite <i>cancelar</i> para sair.",
            parse_mode=ParseMode.HTML
        )

    elif query.data == "jobqueue_set_target":
        context.user_data["aguardando_jobqueue_target"] = True
        await query.message.edit_text(
            "🎯 Envie o <b>@canal</b> ou <b>ID numérico</b> do destino.\n\nDigite <i>cancelar</i> para sair.",
            parse_mode=ParseMode.HTML
        )

    elif query.data == "jobqueue_activate":
        settings = _get_job_settings(context)
        if not settings["target"]:
            await query.message.reply_text("⚠️ Defina o destino antes de ativar a automação.")
            return

        settings["active"] = True
        try:
            _agendar_jobqueue(context.application)
            await query.message.edit_text(
                "▶️ Automação ativada com sucesso.",
                reply_markup=_render_jobqueue_menu(context)[1]
            )
        except Exception as e:
            settings["active"] = False
            logging.error(f"Erro ao ativar JobQueue: {e}")
            await query.message.reply_text("❌ Não foi possível ativar a JobQueue. Verifique se a dependência `python-telegram-bot[job-queue]` está instalada.")

    elif query.data == "jobqueue_stop":
        settings = _get_job_settings(context)
        settings["active"] = False
        _clear_jobqueue_jobs(context.application)
        await query.message.edit_text(
            "⏸ Automação parada.",
            reply_markup=_render_jobqueue_menu(context)[1]
        )

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("jobqueue", jobqueue_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    logging.info("Bot iniciado com sucesso e aguardando links...")
    app.run_polling()

if __name__ == "__main__":
    main()
