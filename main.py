import logging
import time

# ================= RESUMO ROBUSTO =================
def resumir_noticia(texto):
    if not texto or len(texto) < 200:
        return "Texto insuficiente para gerar resumo."

    # -------- LIMITE (EVITA ERRO DE TOKEN) --------
    texto = texto[:6000]

    prompt = f"""
Resuma a seguinte notícia em português.

Regras:
- Um único parágrafo
- 4 a 6 frases
- Linguagem jornalística
- Comece direto pelo fato principal

Texto:
{texto}
"""

    # -------- GEMINI (COM RETRY) --------
    for tentativa in range(2):
        try:
            from google import genai

            client = genai.Client(api_key=GEMINI_API_KEY)

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )

            if response and response.text:
                resumo = response.text.strip()

                # validação mínima
                if len(resumo) > 50:
                    return resumo

        except Exception as e:
            logging.warning(f"Gemini erro (tentativa {tentativa+1}): {e}")
            time.sleep(2)

    # -------- FALLBACK (NUNCA FALHA) --------
    try:
        frases = texto.split(". ")
        resumo = ". ".join(frases[:5]).strip()

        if resumo:
            return resumo

    except Exception as e:
        logging.error(f"Fallback erro: {e}")

    return "Não foi possível gerar o resumo."