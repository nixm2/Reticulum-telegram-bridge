#!/usr/bin/env python3
"""
Мост LXMF (MeshChat) <-> Telegram
- Принимает сообщения из LXMF и пересылает их в Telegram
- Принимает сообщения из Telegram и пересылает их в указанный LXMF-адрес
"""

import RNS
import LXMF
import asyncio
import queue
import sys
import logging
import os
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_TOKEN = "TOKEN"
TELEGRAM_CHAT_ID = -00000000

# Сюда вставьте HEX-хеш получателя (например, из MeshChat / Sideband)
# Хеш можно скопировать из логов другого узла или из приложения.
# Длина: 40 символов (20 байт) — пример: "a1b2c3d4e5f6789012345678abcdef1234567890"
LXMF_DESTINATION_HEX = "ЗДЕСЬ_ВАШ_ХЕШ_ПОЛУЧАТЕЛЯ"   # <-- ЗАМЕНИТЕ!
# ================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LXMFBridge")

# Очередь для сообщений из LXMF → Telegram
q_lxmf_to_tg = queue.Queue()

# Глобальные объекты LXMF / Reticulum
lxmf_router = None
my_lxmf_destination = None   # наш собственный LXMF Destination
my_identity = None

def delivery_callback(message):
    """Вызывается при получении нового LXMF-сообщения."""
    try:
        content = message.content_as_string()
        source_hash = RNS.prettyhexrep(message.source_hash)
        if content:
            logger.info(f"Получено сообщение от {source_hash}: {content}")
            q_lxmf_to_tg.put_nowait(f"От {source_hash[:8]}: {content}")
        else:
            logger.info(f"Получено пустое сообщение от {source_hash}")
    except Exception as e:
        logger.error(f"Ошибка в callback: {e}")

def setup_lxmf():
    global lxmf_router, my_lxmf_destination, my_identity

    # 1. Инициализация Reticulum (конфиг ~/.reticulum/config)
    RNS.Reticulum()
    logger.info("Стек Reticulum инициализирован.")

    # 2. Загрузка или создание Identity для моста
    identity_path = os.path.expanduser("~/.reticulum/my_bridge_lxmf_identity.key")
    if os.path.exists(identity_path):
        try:
            my_identity = RNS.Identity.from_file(identity_path)
            logger.info(f"Загружен существующий Identity. Хеш: {my_identity.hash.hex()}")
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}. Создаём новый.")
            my_identity = RNS.Identity()
            my_identity.to_file(identity_path)
            logger.info(f"Создан новый Identity. Хеш: {my_identity.hash.hex()}")
    else:
        my_identity = RNS.Identity()
        os.makedirs(os.path.dirname(identity_path), exist_ok=True)
        my_identity.to_file(identity_path)
        logger.info(f"Создан и сохранён новый Identity. Хеш: {my_identity.hash.hex()}")

    # 3. Создаём LXMF-роутер и регистрируем наш Identity
    lxmf_router = LXMF.LXMRouter(storagepath="./lxmf_storage")
    my_lxmf_destination = lxmf_router.register_delivery_identity(my_identity, display_name="TG Bridge")
    lxmf_router.register_delivery_callback(delivery_callback)

    # 4. Анонсируем наш адрес в сеть
    lxmf_router.announce(my_lxmf_destination.hash)

    logger.info("="*40)
    logger.info(f"Ваш LXMF-адрес (хеш): {RNS.prettyhexrep(my_lxmf_destination.hash)}")
    logger.info("Теперь вы можете найти этот мост в MeshChat или Sideband по этому адресу.")
    logger.info("="*40)

def send_lxmf_message(dest_hex: str, text: str) -> bool:
    """
    Отправляет текстовое сообщение в указанный LXMF-адрес.
    dest_hex: hex-строка хеша получателя (20 байт → 40 символов)
    """
    try:
        dest_bytes = bytes.fromhex(dest_hex)

        # Восстанавливаем Identity получателя по его хешу
        recipient_identity = RNS.Identity.recall(dest_bytes)
        if recipient_identity is None:
            logger.error(f"Не найден Identity получателя {dest_hex[:8]}. Убедитесь, что он известен в сети.")
            return False

        # Создаём RNS.Destination для получателя
        dest_destination = RNS.Destination(
            recipient_identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            "lxmf",
            "delivery"
        )

        # Создаём LXMF-сообщение
        message = LXMF.LXMessage(
            dest_destination,
            my_lxmf_destination,
            text
        )

        # Отправляем через роутер
        lxmf_router.handle_outbound(message)
        logger.info(f"LXMF сообщение отправлено получателю {dest_hex[:8]}: {text[:50]}...")
        return True

    except Exception as e:
        logger.error(f"Ошибка отправки LXMF: {e}", exc_info=True)
        return False

async def process_lxmf_to_tg(app):
    """Фоновая задача: пересылка сообщений из очереди LXMF → Telegram"""
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
    """Обработчик сообщений из Telegram → отправляет их в LXMF."""
    if not update.message or not update.message.text:
        return
    msg = update.message.text.strip()
    if not msg:
        return

    user = update.message.from_user
    username = user.username or user.first_name or str(user.id)
    logger.info(f"TG @{username}: {msg}")

    # Проверяем, задан ли адрес получателя
    if LXMF_DESTINATION_HEX == "ЗДЕСЬ_ВАШ_ХЕШ_ПОЛУЧАТЕЛЯ" or not LXMF_DESTINATION_HEX:
        await update.message.reply_text("❌ Ошибка: не указан адрес получателя LXMF в скрипте.")
        return

    # Отправляем в LXMF в отдельном потоке (не блокируем Telegram)
    success = await asyncio.to_thread(send_lxmf_message, LXMF_DESTINATION_HEX, msg)

    if success:
        await update.message.reply_text("✅ Сообщение отправлено в сеть Reticulum (LXMF).")
    else:
        await update.message.reply_text("❌ Не удалось отправить сообщение. Проверьте логи.")

async def post_init(app):
    app.create_task(process_lxmf_to_tg(app))

def main():
    try:
        setup_lxmf()
        app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tg_message_handler))

        logger.info("Мост ЗАПУЩЕН: приём LXMF → Telegram, отправка Telegram → LXMF")
        app.run_polling()
    except KeyboardInterrupt:
        logger.info("\nОстановка...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
