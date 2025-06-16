# telegram_flask_verification_bot.py

import os
import logging
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, ChatJoinRequestHandler, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode

# --- Load Environment Variables ---
load_dotenv()

# --- Configure Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Configuration Variables ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Admin chat ID where verification details will be sent.
# Make sure this is set in your .env file or Render's environment variables.
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# Determine WEBHOOK_URL based on Render environment or .env
RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/webhook"
else:
    # Fallback for local development or if not on Render
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://127.0.0.1:5000/webhook")
    logger.warning("RENDER_EXTERNAL_HOSTNAME not found, falling back to WEBHOOK_URL from .env or default.")

PORT = int(os.getenv("PORT", 5000))

# --- Flask App Initialization ---
app = Flask(__name__)

# --- python-telegram-bot Application Setup ---
application = Application.builder().token(BOT_TOKEN).arbitrary_callback_data(True).build()

# Dictionary to store pending join requests awaiting verification.
pending_join_requests = {}

# --- Telegram Bot Handlers (Logic) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /start command. Sends a welcome message.
    """
    user = update.effective_user
    if user:
        await update.message.reply_html(
            rf"Hi {user.mention_html()}! I manage group join requests. "
            "If you're trying to join a group, I'll send you a verification message here first."
        )
        logger.info(f"User {user.id} started the bot in DM.")
    else:
        logger.warning("Received start command without effective user.")

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles new chat join requests. Stores the request and prompts for verification in DM.
    """
    chat_join_request = update.chat_join_request
    user = chat_join_request.from_user
    chat = chat_join_request.chat

    logger.info(
        f"Received join request for chat '{chat.title}' (ID: {chat.id}) "
        f"from user '{user.full_name}' (ID: {user.id}). Storing for verification."
    )

    pending_join_requests[user.id] = chat_join_request

    keyboard = [
        [KeyboardButton("I am not a bot", request_contact=True)]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        one_time_keyboard=True,
        resize_keyboard=True
    )

    verification_message_text = (
        f"Welcome! To complete your request to join '{chat.title}' and verify you are not a bot, "
        "please tap the button below to share your phone number.\n\n"
        "This helps us ensure a real person is joining. Your phone number "
        "will only be used for verification purposes. Telegram will ask for your confirmation."
    )

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=verification_message_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        logger.info(f"Sent verification prompt to user {user.id} in DM for chat '{chat.title}'.")
    except Exception as e:
        logger.error(
            f"Failed to send verification prompt to user {user.id}. Error: {e}"
        )
        if user.id in pending_join_requests:
            del pending_join_requests[user.id]


async def handle_contact_shared(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles when a user shares their contact (phone number) with the bot.
    Approves the pending join request if one exists for this user and sends details to admin.
    """
    message = update.message
    user = message.from_user
    contact = message.contact

    if contact and contact.user_id == user.id:
        phone_number = contact.phone_number
        logger.info(
            f"User {user.full_name} (ID: {user.id}) successfully shared phone number: {phone_number}. "
            f"User details: First Name: {user.first_name}, Last Name: {user.last_name}, "
            f"Username: @{user.username if user.username else 'N/A'}"
        )

        if user.id in pending_join_requests:
            original_join_request = pending_join_requests.pop(user.id)
            group_name = original_join_request.chat.title

            try:
                # Approve the original join request
                await original_join_request.approve()
                logger.info(
                    f"Approved join request for user '{user.full_name}' (ID: {user.id}) "
                    f"to group '{group_name}' after successful phone verification."
                )

                # Send confirmation to the user
                await message.reply_text(
                    f"Thank you for verifying! Your request to join '{group_name}' has been approved. "
                    "You are all set! You can now access the group.",
                    reply_markup=ReplyKeyboardRemove()
                )

                # --- NEW: Send verified user details to the ADMIN_CHAT_ID ---
                if ADMIN_CHAT_ID: # Check if ADMIN_CHAT_ID is set
                    admin_notification_text = (
                        f"✅ **New User Verified and Joined!**\n"
                        f"**Group:** {group_name}\n"
                        f"**User ID:** `{user.id}`\n"
                        f"**Name:** {user.full_name}\n"
                        f"**Username:** @{user.username if user.username else 'N/A'}\n"
                        f"**Phone:** `{phone_number}`\n"
                        f"[View User Profile](tg://user?id={user.id})" # Link to user's profile
                    )
                    try:
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=admin_notification_text,
                            parse_mode=ParseMode.MARKDOWN_V2 # Use MarkdownV2 for bold and links
                        )
                        logger.info(f"Sent verification notification to admin chat {ADMIN_CHAT_ID} for user {user.id}.")
                    except Exception as admin_notify_error:
                        logger.error(f"Failed to send admin notification for user {user.id}: {admin_notify_error}")
                else:
                    logger.warning("ADMIN_CHAT_ID not set, skipping admin notification.")

            except Exception as e:
                logger.error(
                    f"Failed to approve join request for user {user.id} to group '{group_name}' "
                    f"after verification. Error: {e}"
                )
                await message.reply_text(
                    f"Verification successful, but I encountered an issue approving your request to join '{group_name}'. "
                    "Please contact a group administrator. Apologies for the inconvenience.",
                    reply_markup=ReplyKeyboardRemove()
                )
        else:
            logger.warning(f"User {user.id} shared contact, but no pending join request found for them.")
            await message.reply_text(
                "Thanks for sharing your contact! It seems you're not currently awaiting verification "
                "for a group join request through this bot. If you were trying to join a group, "
                "please try sending the join request again to the group.",
                reply_markup=ReplyKeyboardRemove()
            )
    else:
        logger.warning(f"User {user.id} sent invalid contact data or user_id mismatch.")
        await message.reply_text(
            "It seems like the contact shared was not valid or not your own. "
            "Please tap the 'I am not a bot' button again if it's still there.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("I am not a bot", request_contact=True)]],
                one_time_keyboard=True, resize_keyboard=True
            )
        )

async def fallback_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    A generic message handler for any text message received in private chat.
    """
    user = update.effective_user
    if user and user.id in pending_join_requests:
        await update.message.reply_text(
            "Please complete the verification by tapping the 'I am not a bot' button. "
            "If you don't see it, it might have disappeared; you can type /start or "
            "re-send your group join request to receive the button again.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("I am not a bot", request_contact=True)]],
                one_time_keyboard=True, resize_keyboard=True
            )
        )
    elif user:
        await update.message.reply_text("Hello! I'm here to help with group join requests. How can I assist you?")
    else:
        logger.warning("Received a message without effective user in fallback handler.")

# --- Register Handlers with python-telegram-bot Application ---
application.add_handler(CommandHandler("start", start))
application.add_handler(ChatJoinRequestHandler(handle_join_request))
application.add_handler(MessageHandler(filters.CONTACT & filters.PRIVATE, handle_contact_shared))
application.add_handler(MessageHandler(filters.TEXT & filters.PRIVATE, fallback_message_handler))

# --- Flask Webhook Route ---
@app.route('/webhook', methods=['POST'])
async def webhook():
    if request.method == "POST":
        update_data = request.get_json()
        if update_data:
            update = Update.de_json(update_data, application.bot)
            await application.process_update(update)
        else:
            logger.warning("Received empty or invalid JSON update.")
        return jsonify({"status": "ok"}), 200
    return jsonify({"status": "Method Not Allowed"}), 405

# --- Flask Server Startup & Webhook Setup ---
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("CRITICAL ERROR: Please set your TELEGRAM_BOT_TOKEN environment variable.")
        exit(1)

    # Validate ADMIN_CHAT_ID
    if not ADMIN_CHAT_ID:
        print("WARNING: ADMIN_CHAT_ID environment variable is not set. Admin notifications will be skipped.")
    try:
        # Attempt to convert to int to catch non-numeric IDs early
        _ = int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID else None
    except ValueError:
        print(f"WARNING: ADMIN_CHAT_ID '{ADMIN_CHAT_ID}' is not a valid integer. Admin notifications may fail.")


    # This logic automatically sets the webhook URL for Render deployments.
    # For local development, ensure WEBHOOK_URL is set in .env if not using ngrok.
    if WEBHOOK_URL and RENDER_EXTERNAL_HOSTNAME:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        loop.run_until_complete(
            application.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
        )
        logger.info(f"Telegram webhook set to: {WEBHOOK_URL}")
    elif not RENDER_EXTERNAL_HOSTNAME:
        print("Not on Render, or RENDER_EXTERNAL_HOSTNAME not available. Please ensure webhook is set manually if needed.")
        print(f"For local testing, Flask app will run on port {PORT}. Use ngrok to expose it.")

    logger.info(f"Flask app starting on port {PORT}...")
    # When deployed on Render (or other WSGI servers), Gunicorn will handle app.run()
    # For local testing directly with `python script.py`, this `app.run()` is needed.
    if not RENDER_EXTERNAL_HOSTNAME:
         app.run(port=PORT, debug=False)

