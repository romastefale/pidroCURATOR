# ================= NOVO: LISTA DE AUTORIZADOS =================
AUTHORIZED_USERS = set()

def is_authorized(user):
    if user.id == ADMIN_ID:
        return True
    if user.username and user.username.lower() in AUTHORIZED_USERS:
        return True
    return False

# ================= COMANDO /convidado =================
async def convidado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Apenas o administrador pode adicionar convidados.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Use: /convidado @usuario")
        return

    username = context.args[0].replace("@", "").lower()
    AUTHORIZED_USERS.add(username)

    await update.message.reply_text(f"✅ @{username} foi autorizado a usar o bot.")

# ================= COMANDO /revoke =================
async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Apenas o administrador pode remover convidados.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Use: /revoke @usuario")
        return

    username = context.args[0].replace("@", "").lower()

    if username in AUTHORIZED_USERS:
        AUTHORIZED_USERS.remove(username)
        await update.message.reply_text(f"❌ @{username} foi removido dos autorizados.")
    else:
        await update.message.reply_text("⚠️ Usuário não estava autorizado.")

# ================= ALTERAÇÕES NOS HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user):
        await update.message.reply_text("⛔ Acesso não autorizado.")
        return
    
    context.user_data.clear()
    await update.message.reply_text(
        "🤖 <b>pidroCURATOR está on!</b>\n\n<i>📝 Envie o link de uma notícia para começarmos.</i>",
        parse_mode=ParseMode.HTML
    )

async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_authorized(update.effective_user):
        return

    # RESTANTE DO CÓDIGO SEGUE IGUAL...

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_authorized(query.from_user):
        await query.message.reply_text("⛔ Acesso não autorizado.")
        return

    # RESTANTE DO CÓDIGO SEGUE IGUAL...

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("convidado", convidado))  # NOVO
    app.add_handler(CommandHandler("revoke", revoke))        # NOVO
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))

    logging.info("Bot iniciado com sucesso e aguardando links...")
    app.run_polling()