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
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
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
CHANNEL_ID = os.environ.get("CHANNEL_ID") # <-- ID do canal para onde a notícia vai

if not TOKEN:
    logging.error("A variável de ambiente TELEGRAM_TOKEN não foi encontrada!")
    exit(1)

bot = telebot.TeleBot(TOKEN, threaded=True)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Dicionário para armazenar as notícias geradas temporariamente (Rascunhos)
DRAFTS = {}

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
    except Exception as e:
        try:
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            r = scraper.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            html_content = r.text
        except Exception as e2:
            return None

    try:
        data = trafilatura.extract(
            html_content, output_format="json", include_comments=False,
            include_tables=False, favor_precision=True
        )
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
    except Exception:
        return None

    return None

# ================= COMANDOS E HANDLERS =================
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
            texto_html = texto_html[:3500] + "...\n\n<i>[Resumo truncado]</i>"
        
        resposta = (
            f'<a href="{url}">&#8203;</a>'
            f"<b>{titulo_escapado}</b>\n\n"
            f"<blockquote>{texto_html}</blockquote>"
        )
        
        # Salva o rascunho formatado vinculado ao ID do usuário
        user_id = message.from_user.id
        DRAFTS[user_id] = resposta
        
        try:
            # Cria o botão de publicar
            markup = InlineKeyboardMarkup()
            btn_publicar = InlineKeyboardButton("✅ Publicar no Canal", callback_data="publish")
            markup.add(btn_publicar)

            bot.edit_message_text(
                chat_id=message.chat.id, 
                message_id=msg_status.message_id, 
                text=resposta, 
                parse_mode="HTML",
                reply_markup=markup
            )
        except Exception as e:
            logging.error(f"Erro ao enviar: {e}")
    else:
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg_status.message_id, text="❌ Não foi possível extrair o texto.")

# ================= CALLBACK DO BOTÃO DE PUBLICAR =================
@bot.callback_query_handler(func=lambda call: call.data == "publish")
def callback_publish(call):
    user_id = call.from_user.id
    
    # Verifica se a variável de ambiente CHANNEL_ID foi configurada
    if not CHANNEL_ID:
        bot.answer_callback_query(call.id, "❌ Erro: Variável CHANNEL_ID não configurada no servidor.", show_alert=True)
        return
        
    # Verifica se o bot tem a mensagem salva na memória
    if user_id in DRAFTS:
        try:
            # Envia a mensagem salva e formatada para o canal
            bot.send_message(chat_id=CHANNEL_ID, text=DRAFTS[user_id], parse_mode="HTML")
            
            # Avisa na tela que deu certo
            bot.answer_callback_query(call.id, "✅ Postagem publicada com sucesso!")
            
            # Remove o botão da mensagem original e adiciona um aviso de "Publicado"
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            novo_texto = call.message.html_text + "\n\n<i>✅ Publicado no canal.</i>"
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=novo_texto, parse_mode="HTML")
            
            # Limpa o rascunho da memória
            del DRAFTS[user_id]
            
        except Exception as e:
            logging.error(f"Erro ao postar no canal: {e}")
            bot.answer_callback_query(call.id, "❌ Erro ao enviar. Verifique se o bot é administrador do canal.", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "⚠️ Rascunho não encontrado ou já publicado.", show_alert=True)

# ================= INICIALIZAÇÃO =================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    logging.info("Iniciando o bot no Telegram...")
    bot.infinity_polling()
