import os
import json
import logging
import requests
import trafilatura
import cloudscraper
from bs4 import BeautifulSoup
import telebot

# ================= CONFIGURAÇÃO DE LOGGING =================
# Essencial para acompanhar os logs no painel do Railway
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ================= CONFIGURAÇÃO DO BOT =================
# Lendo o token das Variáveis de Ambiente do Railway
TOKEN = os.environ.get("TELEGRAM_TOKEN")

if not TOKEN:
    logging.error("A variável de ambiente TELEGRAM_TOKEN não foi encontrada!")
    exit(1)

# O parâmetro threaded=True (que é o padrão) evita o bloqueio síncrono, 
# permitindo que o bot atenda vários usuários ao mesmo tempo.
bot = telebot.TeleBot(TOKEN, threaded=True)

# ================= SCRAPER =================
def scrape(url):
    # Validação rápida de URL
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
        # -------- PRIMEIRA TENTATIVA (requests) --------
        logging.info(f"Iniciando scraping (requests) para: {url}")
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"
        html = r.text

    except Exception as e:
        logging.warning(f"Request padrão falhou, tentando cloudscraper: {e}")

        try:
            # -------- SEGUNDA TENTATIVA (cloudscraper) --------
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            r = scraper.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            html = r.text

        except Exception as e2:
            logging.error(f"Cloudscraper falhou: {e2}")
            return None

    # -------- EXTRAÇÃO COM TRAFILATURA --------
    try:
        data = trafilatura.extract(
            html,
            output_format="json",
            include_comments=False,
            include_tables=False,
            favor_precision=True
        )

        if data:
            parsed = json.loads(data)
            if parsed.get("text"):
                return parsed

    except Exception as e:
        logging.warning(f"Trafilatura falhou: {e}")

    # -------- FALLBACK MANUAL --------
    try:
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        article = soup.find("article")
        if article:
            text = article.get_text(separator=" ", strip=True)
        else:
            text = soup.get_text(separator=" ", strip=True)

        text = " ".join(text.split())

        if len(text) > 200:
            # Correção do bug do Título: usando get_text() no lugar de string
            title = soup.title.get_text(strip=True) if soup.title else "Notícia"
            return {
                "title": title,
                "text": text,
                "sitename": ""
            }

    except Exception as e:
        logging.error(f"Fallback parsing falhou: {e}")

    logging.error(f"Falha total no scraping para a URL: {url}")
    return None

# ================= COMANDOS E HANDLERS =================
@bot.message_handler(commands=['start'])
def start_message(message):
    bot.reply_to(message, "🤖 Envie link…")

@bot.message_handler(func=lambda message: True)
def handle_link(message):
    url = message.text.strip()
    
    # Envia a mensagem de feedback imediato
    msg_status = bot.reply_to(message, "⏳ Processando...")
    
    # Executa a extração
    resultado = scrape(url)
    
    if resultado and resultado.get("text"):
        titulo = resultado.get("title", "Sem Título")
        texto = resultado.get("text", "")
        
        # Formata a resposta final
        resposta = f"*{titulo}*\n\n{texto}"
        
        # O Telegram tem um limite de 4096 caracteres por mensagem
        if len(resposta) > 4000:
            resposta = resposta[:4000] + "...\n\n_[Texto truncado pelo limite do Telegram]_"
            
        # Edita a mensagem de "Processando..." com o resultado final
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg_status.message_id, text=resposta, parse_mode="Markdown")
    else:
        # Se falhar, edita a mensagem avisando o usuário
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg_status.message_id, text="❌ Não foi possível extrair o texto deste link. O site pode estar bloqueando o acesso.")

# ================= INICIALIZAÇÃO =================
if __name__ == "__main__":
    logging.info("Iniciando o bot no Telegram...")
    # infinity_polling roda continuamente e reinicia caso haja quedas de rede
    bot.infinity_polling()
