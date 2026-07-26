"""
Copy this to secrets_local.py and fill it in.  secrets_local.py is gitignored
and must never be committed — this repository is public.

(Named secrets_local, not secrets, because a local secrets.py would shadow
Python's standard-library `secrets` module.)

If a token ever does reach a commit, rewriting history is not enough: assume it
is compromised and revoke it with @BotFather.
"""

# Telegram bot token, from @BotFather — looks like "123456789:AAE..."
TELEGRAM_TOKEN = ""

# The chat to notify. Get it by messaging your bot, then opening
#   https://api.telegram.org/bot<TOKEN>/getUpdates
# and reading result[].message.chat.id
TELEGRAM_CHAT_ID = ""

# Notifications are simply skipped when these are empty, so the app runs fine
# without them.
