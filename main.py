# ================= SCRAPER =================
def scrape(url):
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
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        html = r.text

    except Exception as e:
        logging.warning(f"Request padrão falhou, tentando cloudscraper: {e}")

        try:
            import cloudscraper
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            r = scraper.get(url, headers=headers, timeout=15)
            r.raise_for_status()
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
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # Remove scripts e styles
        for tag in soup(["script", "style", "noscript"]):
            tag.extract()

        # Tenta pegar <article>
        article = soup.find("article")
        if article:
            text = article.get_text(separator=" ", strip=True)
        else:
            # fallback geral
            text = soup.get_text(separator=" ", strip=True)

        # Limpeza básica
        text = " ".join(text.split())

        if len(text) > 200:
            title = soup.title.string.strip() if soup.title else "Notícia"
            return {
                "title": title,
                "text": text,
                "sitename": ""
            }

    except Exception as e:
        logging.error(f"Fallback parsing falhou: {e}")

    logging.error("Falha total no scraping")
    return None
