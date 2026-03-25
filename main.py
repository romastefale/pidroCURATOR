        mensagem = formatar(titulo, resumo, fonte, url)

        # ================= IMAGEM (APENAS PARA LAYOUT) =================
        imagem_url = None
        try:
            soup = BeautifulSoup(html, "html.parser")
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                imagem_url = og_img.get("content")
        except:
            pass

        from telegram import InlineKeyboardMarkup, InlineKeyboardButton

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Ler matéria", url=url)]
        ])

        # ================= ENVIO (ALTERADO APENAS LAYOUT) =================
        if imagem_url:
            await update.message.reply_photo(
                photo=imagem_url,
                caption=mensagem,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text(
                mensagem,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=keyboard
            )