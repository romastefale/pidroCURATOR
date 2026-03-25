import os
import json
import logging
import requests
import trafilatura
import cloudscraper
import html
from bs4 import BeautifulSoup
import telebot
from openai import OpenAI  # <--- IMPORT DA OPENAI ADICIONADO

# ================= CONFIGURAÇÃO DE LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ================= CONFIGURAÇÕES E CHAVES =================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not TOKEN:
    logging.error("A variável de ambiente TELEGRAM_TOKEN não foi encontrada!")
    exit(1)

# Inicializa o bot do Telegram
bot = telebot.TeleBot(TOKEN, threaded=True)

# Inicializa o cliente da OpenAI
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

if not client:
    logging.warning("OPENAI_API_KEY não encontrada! O bot não conseguirá gerar resumos.")

# ================= INTEGRAÇÃO OPENAI =================
def summarize_text(title, text):
    """Envia o texto bruto para a OpenAI gerar o post blindado contra alucinações."""
    if not client:
        return "⚠️ Erro interno: OPENAI_API_KEY não está configurada no servidor."

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
            temperature=0.2 # Temperatura baixa para garantir zero alucinação
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Erro na OpenAI: {e}")
        return "❌ Ocorreu um erro ao processar o resumo com a Inteligência Artificial."

# ================= SCRAPER (MANTIDO INTACTO) =================
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
        logging.info(f"Iniciando scraping (requests) para: {url}")
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"
        html_content = r.text
    except Exception as e:
        logging.warning(f"Request padrão falhou, tentando cloudscraper: {e}")
        try:
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            r = scraper.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            html_content = r.text
        except Exception as e2:
            logging.error(f"Cloudscraper falhou: {e2}")
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
    except Exception as e:
        logging.warning(f"Trafilatura falhou: {e}")

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

    logging.error(f"Falha total no scraping para a URL: {url}")
    return None

# ================= COMANDOS E HANDLERS =================
@bot.message_handler(commands=['start'])
def start_message(message):
    bot.reply_to(message, "🤖 Envie o link da notícia para eu gerar o resumo...")

@bot.message_handler(func=lambda message: True)
def handle_link(message):
    url = message.text.strip()
    
    # Etapa 1: Feedback inicial
    msg_status = bot.reply_to(message, "⏳ Lendo o link da notícia...")
    
    # Etapa 2: Extrai o texto do site
    resultado = scrape(url)
    
    if resultado and resultado.get("text"):
        titulo = resultado.get("title", "Sem Título")
        texto_bruto = resultado.get("text", "")
        
        # Atualiza o status para o usuário
        bot.edit_message_text(chat_id=message.chat.id, message_id=msg_status.message_id, text="🧠 Resumindo conteúdo com Inteligência Artificial...")
        
        # Etapa 3: Pede para a OpenAI formatar
        texto_resumido = summarize_text(titulo, texto_bruto)
        
        # Etapa 4: Montagem do HTML com o truque do Link Invisível e Blockquote
        titulo_escapado = html.escape(titulo)
        # Substitui quebras de linha normais do GPT para garantir a indentação HTML
        texto_html = html.escape(texto_resumido).replace("\n", "<br>") 
        
        resposta = (
            f'<a href="{
