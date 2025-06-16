# telegram_flask_verification_bot.py

import os
import logging
import json
import asyncio
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, ChatJoinRequestHandler, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode
from telegram.ext.filters import ChatType

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
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# Determine WEBHOOK_URL based on Render environment or .env
# RENDER_EXTERNAL_HOSTNAME is provided by Render.com
RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
if RENDER_EXTERNAL_HOSTNAME:
    WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/webhook"
else:
    # Fallback for local development or if not on Render
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://127.0.0.1:5000/webhook")
    logger.warning("RENDER_EXTERNAL_HOSTNAME not found, falling back to WEBHOOK_URL from .env or default for local testing.")

PORT = int(os.getenv("PORT", 5000))

# --- Flask App Initialization ---
app = Flask(__name__)

# --- python-telegram-bot Application Setup ---
# Build the application instance. We will configure webhook in post_init callback.
application = Application.builder().token(BOT_TOKEN).arbitrary_callback_data(True).build()

# Dictionary to store pending join requests awaiting verification.
# For a production bot, consider using a database (like Redis or PostgreSQL) for persistence.
pending_join_requests = {}

# --- Callbacks for Application Lifecycle ---

async def post_init_callback(application: Application) -> None:
    """
    Callback function that runs once after the Application has been initialized.
    Used to set the Telegram webhook automatically.
    """
    logger.info("Running post_init_callback: Setting Telegram webhook...")
    if not BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN environment variable is not set. Cannot set webhook.")
        return

    if not WEBHOOK_URL:
        logger.critical("WEBHOOK_URL not determined. Cannot set webhook automatically.")
        return

    try:
        # Clear any old webhooks first to avoid conflicts
        await application.bot.set_webhook(url="")
        logger.info("Cleared any old webhooks successfully.")

        # Set the new webhook
        await application.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
        logger.info(f"Telegram webhook successfully set to: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Failed to set Telegram webhook in post_init: {e}", exc_info=True)
        # In a production environment, you might want to raise an exception here
        # to prevent the service from starting incorrectly without a webhook.

async def post_shutdown_callback(application: Application) -> None:
    """
    Callback function that runs once before the Application shuts down.
    Used to clear the Telegram webhook. (Good practice for clean shutdowns)
    """
    logger.info("Running post_shutdown_callback: Clearing Telegram webhook...")
    try:
        await application.bot.set_webhook(url="")
        logger.info("Telegram webhook cleared successfully on shutdown.")
    except Exception as e:
        logger.error(f"Failed to clear Telegram webhook in post_shutdown: {e}", exc_info=True)

# Register the lifecycle callbacks
application.post_init(post_init_callback)
application.post_shutdown(post_shutdown_callback)

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
                await original_join_request.approve()
                logger.info(
                    f"Approved join request for user '{user.full_name}' (ID: {user.id}) "
                    f"to group '{group_name}' after successful phone verification."
                )

                await message.reply_text(
                    f"Thank you for verifying! Your request to join '{group_name}' has been approved. "
                    "You are all set! You can now access the group.",
                    reply_markup=ReplyKeyboardRemove()
                )

                if ADMIN_CHAT_ID:
                    admin_notification_text = (
                        f"✅ **New User Verified and Joined!**\n"
                        f"**Group:** {group_name}\n"
                        f"**User ID:** `{user.id}`\n"
                        f"**Name:** {user.full_name}\n"
                        f"**Username:** @{user.username if user.username else 'N/A'}\n"
                        f"**Phone:** `{phone_number}`\n"
                        f"[View User Profile](tg://user?id={user.id})"
                    )
                    try:
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=admin_notification_text,
                            parse_mode=ParseMode.MARKDOWN_V2
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
application.add_handler(MessageHandler(filters.CONTACT & ChatType.PRIVATE, handle_contact_shared))
application.add_handler(MessageHandler(filters.TEXT & ChatType.PRIVATE, fallback_message_handler))

# --- Flask Webhook Route ---
@app.route('/webhook', methods=['POST'])
async def webhook():
    """
    This is the Flask endpoint that Telegram sends updates to.
    It receives the JSON update and passes it to application.update_queue.
    """
    if request.method == "POST":
        try:
            update_data_json = request.get_data().decode('utf-8')
            # logger.info(f"Received raw webhook update: {update_data_json[:200]}...") # Log first 200 chars

            # Put the update into PTB's queue for processing.
            await application.update_queue.put(Update.de_json(json.loads(update_data_json), application.bot))

            return jsonify({"status": "ok"}), 200
        except Exception as e:
            logger.error(f"Error processing webhook update: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "Method Not Allowed"}), 405

# --- Optional: Root Route for Flask (for health checks) ---
@app.route('/', methods=['GET'])
def root_route():
    """
    Handles GET requests to the root URL (/).
    Provides a simple status message for the web service.
    """
    status_message = "Telegram Bot Webhook Listener is Live and Operational!"
    logger.info(f"Root route accessed. Status: {status_message}")
    # Return plain text status, Render expects a 200 OK response for healthy service
    return status_message, 200

# --- Flask Server Startup (for local development ONLY) ---
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable is not set. Bot cannot start locally.")
        exit(1)
    
    if not ADMIN_CHAT_ID:
        print("WARNING: ADMIN_CHAT_ID environment variable is not set. Admin notifications will be skipped.")
    try:
        # Attempt to convert to int to catch non-numeric IDs early
        _ = int(ADMIN_CHAT_ID) if ADMIN_CHAT_ID else None
    except ValueError:
        print(f"WARNING: ADMIN_CHAT_ID '{ADMIN_CHAT_ID}' is not a valid integer. Admin notifications may fail.")

    logger.info("Starting local development server with PTB Application...")

    # This manually starts PTB's webhook server for local development.
    # On Render, Gunicorn handles the server, and PTB's Application callbacks handle the webhook setting.
    async def run_local_webhook_server():
        # Clear any existing webhooks for local testing
        await application.bot.set_webhook(url="")
        logger.info("Cleared any existing webhooks for local testing.")

        # Run PTB's webhook server locally
        await application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="/webhook",
            webhook_url=WEBHOOK_URL # For local testing, this is typically your ngrok URL
        )

    try:
        asyncio.run(run_local_webhook_server())
    except KeyboardInterrupt:
        logger.info("Local bot stopped by user.")
    except Exception as e:
        logger.error(f"Error running local bot: {e}", exc_info=True)

