import os
import json
import logging
import requests
import trafilatura
import cloudscraper
import html
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from bs4 import BeautifulSoup
import telebot
from telebot.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    InlineQueryResultArticle, 
    InputTextMessageContent
)
from openai import OpenAI

# ================= CONFIGURAÇÃO DE LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ================= SERVIDOR DUMMY (PARA O RAILWAY) =================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write("Bot Ativo".encode("utf-8"))
    def log_message(self, format, *args):
        pass

def run_dummy_server():
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    logging.info(f"Servidor Dummy rodando na porta {port}")
    server.serve_forever()

# ================= CONFIGURAÇÕES E CHAVES =================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not TOKEN:
    logging.error("A variável de ambiente TELEGRAM_TOKEN não foi encontrada!")
    exit(1)

bot = telebot.TeleBot(TOKEN, threaded=True)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

if not client:
    logging.warning("OPENAI_API_KEY não encontrada! O bot não conseguirá gerar resumos.")

# ================= CACHE DE USUÁRIOS =================
# Armazena temporariamente a última notícia gerada por cada usuário
# para que o bot saiba o que compartilhar quando o botão for clicado.
USER_CACHE = {}

# ================= INTEGRAÇÃO OPENAI =================
def summarize_text(title, text):
    if not client:
        return "⚠️ Erro interno: OPENAI_API_KEY não está configurada."

    system_prompt = (
        "Você é um editor-chefe de jornalismo rigoroso e imparcial. "
        "Sua tarefa é ler o texto bruto de uma matéria e reescrevê-lo em um post atraente para o Telegram. "
        "REGRAS ABSOLUTAS: "
        "1. Use APENAS as informações explícitas no texto fornecido. "
        "2. NUNCA invente, deduza ou adicione dados, nomes ou números externos (Zero Alucinação). "
        "3. Se o texto estiver confuso, limite-se aos fatos claros. "
        "4. Entregue o texto limpo, sem colocar o título (eu já o colocarei via código). "
        "5. O seu resumo DEVE conter os seguintes tópicos (caso a informação exista no texto):\n"
        "📌 Mais detalhes\n"
        "📊 Impacto\n"
        "🔎 Contexto"
    )

    user_prompt = f"TÍTULO ORIGINAL: {title}\n\nTEXTO BRUTO:\n{text}"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Erro na OpenAI: {e}")
        return "❌ Ocorreu um erro ao processar o resumo com a Inteligência Artificial."

# ================= SCRAPER =================
def scrape(url):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"
        html_content = r.text
    except Exception:
        try:
            scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
            r = scraper.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            html_content = r.text
        except Exception as e2:
            logging.error(f"Cloudscraper falhou: {e2}")
            return None

    try:
        data = trafilatura.extract(html_content, output_format="json", include_comments=False, include_tables=False, favor_precision=True)
        if data:
            parsed = json.loads(data)
            if parsed.get("text"):
                return parsed
    except Exception:
        pass

    try:
        soup = BeautifulSoup(html_content, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        article = soup.find("article")
        if article:
            text = article.get_text(separator=" ", strip=True)
        else:
            text = soup.get_text(separator=" ", strip=True)
        
        text = " ".join(text.split())
        
        if len(text) > 200:
            title = soup.title.get_text(strip=True) if soup.title else "Notícia"
            return {"title": title, "text": text, "sitename": ""}
    except Exception as e:
        logging.error(f"Fallback parsing falhou: {e}")

    return None

# ================= COMANDOS E HANDLERS =================

# 1. Handler do Compartilhamento (Inline Mode)
@bot.inline_handler(func=lambda query: query.query == "share")
def handle_inline_share(inline_query):
    user_id = inline_query.from_user.id
    resposta_formatada = USER_CACHE.get(user_id)
    
    if not resposta_formatada:
        return
        
    # Prepara o conteúdo exato com a formatação HTML
    conteudo = InputTextMessageContent(
        message_text=resposta_formatada,
        parse_mode="HTML"
    )
    
    # Cria o "card" que você vai clicar para confirmar o envio
    artigo = InlineQueryResultArticle(
        id="share_news",
        title="📰 Publicar Notícia",
        description="Toque aqui para enviar a notícia formatada para este chat.",
        input_message_content=conteudo
    )
    
    bot.answer_inline_query(inline_query.id, [artigo], cache_time=1)

# 2. Handlers Normais de Mensagem
@bot.message_handler(commands=['start'])
def start_message(message):
    bot.reply_to(message, "🤖 Envie o link da notícia para eu gerar o resumo...")

@bot.message_handler(func=lambda message: True)
def handle_link(message):
    url = message.text.strip()
    
    msg_status = bot.reply_to(message, "⏳ Lendo o link da notícia...")
    resultado = scrape(url)
    
    if resultado and resultado.get("text"):
        titulo = resultado.get("title", "Sem Título")
        texto_bruto = resultado.get("text", "")
        
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg_status.message_id, text="🧠 Resumindo conteúdo com Inteligência Artificial...")
        
        texto_resumido = summarize_text(titulo, texto_bruto)
        
        titulo_escapado = html.escape(titulo)
        texto_html = html.escape(texto_resumido)
        
        if len(texto_html) > 3500:
            texto_html = texto_html[:3500] + "...\n\n<i>[Resumo truncado pelo limite de tamanho]</i>"
        
        resposta = (
            f'<a href="{url}">&#8203;</a>'
            f"<b>{titulo_escapado}</b>\n\n"
            f"<blockquote>{texto_html}</blockquote>"
        )
        
        # Salva o resultado no cache do usuário para o Inline Query poder acessar depois
        USER_CACHE[message.from_user.id] = resposta
        
        try:
            # -------- CRIAÇÃO DO BOTÃO DE COMPARTILHAMENTO --------
            markup = InlineKeyboardMarkup()
            # O parâmetro switch_inline_query abre a seleção de chats do Telegram
            btn_compartilhar = InlineKeyboardButton(
                "↗️ Compartilhar", 
                switch_inline_query="share"
            )
            markup.add(btn_compartilhar)
            # ------------------------------------------------------

            bot.edit_message_text(
                chat_id=message.chat.id, 
                message_id=msg_status.message_id, 
                text=resposta, 
                parse_mode="HTML",
                reply_markup=markup
            )
        except Exception as e:
            logging.error(f"Erro ao enviar a mensagem final: {e}")
            bot.edit_message_text(chat_id=message.chat.id, message_id=msg_status.message_id, text="❌ Ocorreu um erro ao formatar a mensagem visualmente para o Telegram.")
    else:
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg_status.message_id, text="❌ Não foi possível extrair o texto deste link. Pode haver um bloqueio de acesso.")

# ================= INICIALIZAÇÃO =================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    logging.info("Iniciando o bot no Telegram...")
    bot.infinity_polling()
