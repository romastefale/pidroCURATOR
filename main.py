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
from google import genai
from google.genai import types

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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TOKEN:
    logging.error("A variável de ambiente TELEGRAM_TOKEN não foi encontrada!")
    exit(1)

# Inicializa o bot
bot = telebot.TeleBot(TOKEN, threaded=True)

# Inicializa o cliente do Gemini (Ajustado para API v1 estável)
client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options={'api_version': 'v1'}
        )
    except Exception as e:
        logging.error(f"Erro ao configurar cliente Gemini: {e}")
else:
    logging.warning("GEMINI_API_KEY não encontrada!")

# ================= INTEGRAÇÃO GEMINI =================
def summarize_text(title, text):
    if not client:
        return "⚠️ Erro interno: GEMINI_API_KEY não está configurada."

    system_prompt = (
        "Você é um editor-chefe de jornalismo rigoroso e imparcial. "
        "Sua tarefa é ler o texto bruto de uma matéria e reescrevê-lo em um post atraente para o Telegram. "
        "REGRAS ABSOLUTAS: "
        "1. Use APENAS as informações explícitas no texto fornecido. "
        "2. NUNCA adicione dados externos. "
        "3. O seu resumo DEVE conter: 📌 Mais detalhes, 📊 Impacto e 🔎 Contexto."
    )

    user_prompt = f"TÍTULO ORIGINAL: {title}\n\nTEXTO BRUTO:\n{text}"

    try:
        # Chamada ajustada com Safety Settings para evitar erros de bloqueio
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2,
                safety_settings=[
                    types.SafetySetting(category='HARM_CATEGORY_HATE_SPEECH', threshold='BLOCK_NONE'),
                    types.SafetySetting(category='HARM_CATEGORY_HARASSMENT', threshold='BLOCK_NONE'),
                    types.SafetySetting(category='HARM_CATEGORY_SEXUALLY_EXPLICIT', threshold='BLOCK_NONE'),
                    types.SafetySetting(category='HARM_CATEGORY_DANGEROUS_CONTENT', threshold='BLOCK_NONE'),
                ]
            ),
        )
        
        if not response.text:
            return "❌ O Gemini não conseguiu processar esta notícia (conteúdo possivelmente bloqueado)."
            
        return response.text.strip()
    except Exception as e:
        logging.error(f"Erro no Gemini: {e}")
        return "❌ Ocorreu um erro ao processar o resumo com a Inteligência Artificial."

# ================= SCRAPER =================
def scrape(url):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }

    try:
        logging.info(f"Iniciando scraping para: {url}")
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        html_content = r.text
    except Exception as e:
        logging.warning(f"Request falhou, tentando cloudscraper: {e}")
        try:
            scraper = cloudscraper.create_scraper()
            r = scraper.get(url, headers=headers, timeout=15)
            html_content = r.text
        except Exception as e2:
            logging.error(f"Cloudscraper falhou: {e2}")
            return None

    try:
        data = trafilatura.extract(html_content, output_format="json")
        if data:
            parsed = json.loads(data)
            if parsed.get("text"):
                return parsed
    except:
        pass

    # Fallback simples com BeautifulSoup
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        text = " ".join([p.get_text() for p in soup.find_all("p")])
        if len(text) > 200:
            return {"title": soup.title.string if soup.title else "Notícia", "text": text}
    except:
        return None

# ================= HANDLERS =================
@bot.message_handler(commands=['start'])
def start_message(message):
    bot.reply_to(message, "🤖 Envie o link da notícia para eu gerar o resumo...")

@bot.message_handler(func=lambda message: True)
def handle_link(message):
    url = message.text.strip()
    if not url.startswith("http"):
        return

    msg_status = bot.reply_to(message, "⏳ Lendo o link da notícia...")
    resultado = scrape(url)
    
    if resultado and resultado.get("text"):
        titulo = resultado.get("title", "Sem Título")
        texto_bruto = resultado.get("text", "")
        
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg_status.message_id, text="🧠 Resumindo conteúdo...")
        
        texto_resumido = summarize_text(titulo, texto_bruto)
        
        titulo_escapado = html.escape(titulo)
        texto_html = html.escape(texto_resumido)
        
        if len(texto_html) > 3500:
            texto_html = texto_html[:3500] + "..."
        
        resposta = (
            f'<a href="{url}">&#8203;</a>'
            f"<b>{titulo_escapado}</b>\n\n"
            f"<blockquote>{texto_html}</blockquote>"
        )
        
        try:
            bot.edit_message_text(chat_id=message.chat.id, message_id=msg_status.message_id, text=resposta, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Erro no envio: {e}")
            bot.send_message(message.chat.id, "❌ Erro ao formatar mensagem.")
    else:
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg_status.message_id, text="❌ Falha ao extrair texto do link.")

# ================= INICIALIZAÇÃO =================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    logging.info("Bot rodando...")
    bot.infinity_polling()
