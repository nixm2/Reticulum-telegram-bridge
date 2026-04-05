#!/usr/bin/env python3
"""
Мост LXMF (MeshChat) -> Telegram
Принимает сообщения как LXMF-узел и пересылает их в Telegram.
"""
import RNS
import LXMF
import asyncio
import queue
import sys
import logging
import os
import time
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# === КОНФИГУРАЦИЯ ===
TELEGRAM_TOKEN = "ВАШ ТОКЕН"
TELEGRAM_CHAT_ID = -400000000
# === КОНЕЦ КОНФИГУРАЦИИ ===

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LXMFBridge")

# Очередь для сообщений из LXMF в Telegram
q_lxmf_to_tg = queue.Queue()

# Глобальные переменные для хранения объектов LXMF
lxmf_router = None
my_lxmf_destination = None

def delivery_callback(message):
    """Этот callback вызывается при получении нового LXMF-сообщения."""
    try:
        # Получаем текст сообщения
        content = message.content_as_string()
        # Получаем информацию об отправителе (его LXMF-адрес)
        source_hash = RNS.prettyhexrep(message.source_hash)
        
        if content:
            logger.info(f"Получено сообщение от {source_hash}: {content}")
            # Кладём сообщение в очередь для отправки в Telegram
            q_lxmf_to_tg.put_nowait(f"От {source_hash[:8]}: {content}")
        else:
            logger.info(f"Получено пустое сообщение от {source_hash}")

    except Exception as e:
        logger.error(f"Ошибка при обработке callback'а: {e}")

def setup_lxmf():
    global lxmf_router, my_lxmf_destination

    # 1. Инициализируем стек Reticulum (он сам подхватит конфиг)
    RNS.Reticulum()
    logger.info("Стек Reticulum инициализирован.")

    # 2. Создаём новый Identity для нашего LXMF-узла (или загружаем существующий)
    identity = None
    identity_path = os.path.expanduser("~/.reticulum/my_bridge_lxmf_identity.key")
    if os.path.exists(identity_path):
        try:
            identity = RNS.Identity.from_file(identity_path)
            logger.info(f"Загружен существующий идентификатор. Хеш: {identity.hash.hex()}")
        except Exception as e:
            logger.error(f"Ошибка загрузки идентификатора: {e}. Будет создан новый.")
            identity = RNS.Identity()
            identity.to_file(identity_path)
            logger.info(f"Создан и сохранён новый идентификатор. Хеш: {identity.hash.hex()}")
    else:
        identity = RNS.Identity()
        os.makedirs(os.path.dirname(identity_path), exist_ok=True)
        identity.to_file(identity_path)
        logger.info(f"Создан и сохранён новый идентификатор. Хеш: {identity.hash.hex()}")

    # 3. Создаём LXMF-маршрутизатор и регистрируем наш identity для приёма сообщений
    #    storagepath - директория для хранения временных данных маршрутизатора
    lxmf_router = LXMF.LXMRouter(storagepath="./lxmf_storage")
    my_lxmf_destination = lxmf_router.register_delivery_identity(identity, display_name="TG Bridge")
    
    # 4. Устанавливаем callback для обработки входящих сообщений
    lxmf_router.register_delivery_callback(delivery_callback)
    
    # 5. Анонсируем наш LXMF-адрес в сеть, чтобы другие узлы (MeshChat) могли его найти
    lxmf_router.announce(my_lxmf_destination.hash)
    
    logger.info("="*40)
    logger.info("LXMF-узел успешно создан и прослушивает сеть.")
    logger.info(f"Ваш LXMF-адрес (хеш): {RNS.prettyhexrep(my_lxmf_destination.hash)}")
    logger.info("Теперь вы можете найти этот мост в MeshChat или Sideband по этому адресу.")
    logger.info("="*40)

async def process_lxmf_to_tg(app):
    """Фоновая задача: пересылка сообщений из очереди LXMF -> Telegram"""
    while True:
        try:
            msg = q_lxmf_to_tg.get_nowait()
            await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"📡 {msg}")
            logger.info(f"Переслано в Telegram: {msg}")
        except queue.Empty:
            pass
        except Exception as e:
            logger.error(f"Ошибка отправки в Telegram: {e}")
        await asyncio.sleep(0.05)

async def tg_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик сообщений из Telegram (просто заглушка для обратной связи)."""
    if not update.message or not update.message.text:
        return
    msg = update.message.text.strip()
    if not msg:
        return
    user = update.message.from_user
    username = user.username or user.first_name or str(user.id)
    logger.info(f"TG @{username}: {msg}")
    await update.message.reply_text("Этот мост работает только в сторону MeshChat → Telegram. Ваше сообщение не может быть отправлено в Reticulum.")


async def post_init(app):
    app.create_task(process_lxmf_to_tg(app))

def main():
    try:
        setup_lxmf()
        app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tg_message_handler))

        logger.info("МОСТ ЗАПУЩЕН И ГОТОВ К ПРИЁМУ СООБЩЕНИЙ ИЗ MESHCHAT")
        app.run_polling()
    except KeyboardInterrupt:
        logger.info("\nОстановка...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
