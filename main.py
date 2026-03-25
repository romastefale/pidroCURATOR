def summarize_text(title, text):
    if not client:
        return "⚠️ Erro interno: GEMINI_API_KEY não está configurada."

    system_prompt = (
        "Você é um editor-chefe de jornalismo rigoroso e imparcial. "
        "Sua tarefa é ler o texto bruto de uma matéria e reescrevê-lo em um post atraente para o Telegram. "
        "REGRAS ABSOLUTAS: "
        "1. Use APENAS as informações explícitas no texto fornecido. "
        "2. NUNCA invente, deduza ou adicione dados, nomes ou números externos (Zero Alucinação). "
        "3. Se o texto estiver confuso, limite-se aos fatos claros. "
        "4. Entregue o texto limpo, sem colocar o título. "
        "5. O seu resumo DEVE conter os seguintes tópicos (caso existam):\n"
        "📌 Mais detalhes\n📊 Impacto\n🔎 Contexto"
    )

    user_prompt = f"TÍTULO ORIGINAL: {title}\n\nTEXTO BRUTO:\n{text}"

    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.2,
            ),
        )

        # ✅ COMPATIBILIDADE NOVA API
        if hasattr(response, "text") and response.text:
            return response.text.strip()

        # fallback seguro
        try:
            return response.candidates[0].content.parts[0].text.strip()
        except Exception:
            return "❌ Erro ao interpretar resposta da IA."

    except Exception as e:
        logging.error(f"Erro no Gemini: {e}")
        return "❌ Ocorreu um erro ao processar o resumo com a Inteligência Artificial."
