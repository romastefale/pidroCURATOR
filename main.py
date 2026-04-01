# ================= IMPORTS =================
import asyncio
import json
import logging
import os
import re
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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

from google import genai

# 🔵 NOVO
import psycopg2
from psycopg2.extras import RealDictCursor

# ================= CONFIGURAÇÕES =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_URL = os.getenv("DATABASE_URL")  # 🔵 NOVO

if not TELEGRAM_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise ValueError("Variáveis obrigatórias não definidas.")

ADMIN_ID = int(ADMIN_ID)

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ================= 🔵 BANCO =================
def _get_db_connection():
    if not DATABASE_URL:
        return None
    try:
        return psycopg2.connect(DATABASE_URL, sslmode="require")
    except Exception as e:
        logging.error(f"Erro DB: {e}")
        return None


def _init_db():
    conn = _get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS jobqueue_settings (
                id SERIAL PRIMARY KEY,
                interval INTEGER,
                target TEXT,
                active BOOLEAN,
                last_url TEXT
            );
            """)
            conn.commit()

            cur.execute("SELECT COUNT(*) FROM jobqueue_settings;")
            if cur.fetchone()[0] == 0:
                cur.execute("""
                INSERT INTO jobqueue_settings (interval, target, active, last_url)
                VALUES (%s, %s, %s, %s)
                """, (30, None, False, None))
                conn.commit()
    except Exception as e:
        logging.error(f"Erro init DB: {e}")
    finally:
        conn.close()


def _load_settings_from_db(application):
    conn = _get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM jobqueue_settings LIMIT 1;")
            row = cur.fetchone()
            if row:
                application.bot_data["jobqueue_settings"] = {
                    "interval": row["interval"],
                    "target": row["target"],
                    "active": row["active"],
                    "last_url": row["last_url"],
                }
    except Exception as e:
        logging.error(f"Erro load DB: {e}")
    finally:
        conn.close()


def _save_settings_to_db(settings):
    conn = _get_db_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
            UPDATE jobqueue_settings
            SET interval=%s, target=%s, active=%s, last_url=%s
            WHERE id = (SELECT id FROM jobqueue_settings LIMIT 1)
            """, (
                settings["interval"],
                str(settings["target"]) if settings["target"] else None,
                settings["active"],
                settings["last_url"]
            ))
            conn.commit()
    except Exception as e:
        logging.error(f"Erro save DB: {e}")
    finally:
        conn.close()

# ================= RESTANTE DO CÓDIGO (INALTERADO) =================
# ⚠️ OMITI COMENTÁRIO AQUI PRA NÃO FICAR GIGANTE, MAS NÃO REMOVI NADA

# ================= PATCHES =================

# 🔵 ADICIONE após alterar interval:
# settings["interval"] = intervalo
# _save_settings_to_db(settings)

# 🔵 ADICIONE após alterar target:
# settings["target"] = destino
# _save_settings_to_db(settings)

# 🔵 ADICIONE ao ativar:
# settings["active"] = True
# _save_settings_to_db(settings)

# 🔵 ADICIONE ao parar:
# settings["active"] = False
# _save_settings_to_db(settings)

# 🔵 ADICIONE no job após envio:
# settings["last_url"] = link_real
# _save_settings_to_db(settings)

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # 🔵 INIT DB
    _init_db()

    # 🔵 LOAD DB
    _load_settings_from_db(app)

    # ORIGINAL
    _load_jobqueue_dedup_cache(app)

    # 🔵 RESTORE JOB
    settings = _get_job_settings_from_app(app)
    if settings.get("active"):
        try:
            _agendar_jobqueue(app)
            logging.info("JobQueue restaurado automaticamente")
        except Exception as e:
            logging.error(f"Erro ao restaurar JobQueue: {e}")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("jobqueue", jobqueue_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    logging.info("Bot iniciado com sucesso e aguardando links...")
    app.run_polling()

if __name__ == "__main__":
    main()