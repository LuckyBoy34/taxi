import logging
import re
from geopy.distance import geodesic
import requests
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    PicklePersistence,
)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
API_KEY = "e4c998b8-3a31-4106-a0ec-579edca63eeb"
TO_USER_ID = "7617992512"
BOT_TOKEN = "7916151524:AAFFsrlAqDQJ4W8kCHW209YIALbSATttadE"

# Границы Воронежа
VORONEZH_BBOX = {
    "min_lat": 51.53,
    "max_lat": 51.83,
    "min_lon": 39.05,
    "max_lon": 39.40,
}

# Тарифы
TARIFFS = {
    "Стандарт": {"base_fare": 300, "city_km_rate": 55},
    "Эрмитаж": {"base_fare": 400, "city_km_rate": 65},
}

# Состояния диалога
(
    START_ADDRESS,
    END_ADDRESS,
    PHONE_NUMBER,
    TAXI_TYPE,
    CONFIRM_TAXI_TYPE,
    RESTART_ADDRESS,
    REPEAT_ORDER,
) = range(7)


# ====================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======================
def is_in_voronezh(lat: float, lon: float) -> bool:
    return (
        VORONEZH_BBOX["min_lat"] <= lat <= VORONEZH_BBOX["max_lat"]
        and VORONEZH_BBOX["min_lon"] <= lon <= VORONEZH_BBOX["max_lon"]
    )


def get_coordinates(address: str) -> tuple[float, float] | None:
    """
    Получает координаты адреса с добавлением "Воронеж" по умолчанию.
    """
    # Добавляем "Воронеж" к введенному адресу
    full_address = f"{address}, Воронеж"
    logger.info(f"Поиск координат для: {full_address}")

    # Ограничиваем поиск в пределах Воронежа
    bbox = f"{VORONEZH_BBOX['min_lat']},{VORONEZH_BBOX['min_lon']}~{VORONEZH_BBOX['max_lat']},{VORONEZH_BBOX['max_lon']}"
    url = f"https://geocode-maps.yandex.ru/1.x/?geocode={full_address}&format=json&apikey={API_KEY}&bbox={bbox}&lang=ru_RU"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        features = data["response"]["GeoObjectCollection"]["featureMember"]
        if not features:
            logger.warning("Адрес не найден")
            return None

        pos = features[0]["GeoObject"]["Point"]["pos"]
        lon, lat = map(float, pos.split())

        if not is_in_voronezh(lat, lon):
            logger.warning("Координаты вне зоны Воронежа")
            return None

        logger.info(f"Найдены координаты: {lat}, {lon}")
        return (lat, lon)

    except Exception as e:
        logger.error(f"Ошибка геокодирования: {str(e)}")
        return None


def validate_phone_number(phone: str) -> bool:
    return re.match(r"^\+7\s?\d{3}\s?\d{3}\s?\d{2}\s?\d{2}$", phone) is not None


def get_yandex_map_link(start: tuple, end: tuple) -> str:
    return f"https://yandex.ru/maps/?rtext={start[0]},{start[1]}~{end[0]},{end[1]}&rtt=auto"


def calculate_trip_cost(taxi_type: str, distance: float) -> float:
    return TARIFFS[taxi_type]["base_fare"] + distance * TARIFFS[taxi_type]["city_km_rate"]


# ====================== ОБРАБОТЧИКИ КОМАНД ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🚖 Вызвать такси", callback_data="call_taxi")]]
    await update.message.reply_text(
        "Вас приветствует бот TaxiTroika по заказу такси в городе Воронеж🚕.\n"
        "Чтобы заказать такси - выберите соответствующее действие ниже. ⬇️\n"
        "Если произошла ошибка или Вы желаете заказать такси по телефону - обращайтесь по номерам☎️: 200-11-11 ; 200-33-33 ; 200-22-20 .",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ConversationHandler.END


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["__state__"] = START_ADDRESS
    await query.message.reply_text("📍 Введите начальный адрес (пример: ул. Пушкина, 10):")
    return START_ADDRESS


async def start_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = update.message.text
    coords = get_coordinates(address)

    if not coords:
        await update.message.reply_text("❌ Адрес не найден. Введите снова:")
        return START_ADDRESS

    context.user_data["start_address"] = address
    context.user_data["__state__"] = END_ADDRESS
    keyboard = [[InlineKeyboardButton("◀ Назад", callback_data="back")]]
    await update.message.reply_text(
        "✅ Начальный адрес сохранен!\n📍 Введите конечный адрес:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return END_ADDRESS


async def end_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = update.message.text
    coords = get_coordinates(address)

    if not coords:
        await update.message.reply_text("❌ Адрес не найден. Введите снова:")
        return END_ADDRESS

    context.user_data["end_address"] = address
    context.user_data["__state__"] = PHONE_NUMBER
    keyboard = [[InlineKeyboardButton("◀ Назад", callback_data="back")]]
    await update.message.reply_text(
        "📱 Введите ваш телефон (+7 900 123 45 67):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return PHONE_NUMBER


async def phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text

    if not validate_phone_number(phone):
        await update.message.reply_text("❌ Неверный формат! Пример: +7 900 123 45 67")
        return PHONE_NUMBER

    context.user_data["phone_number"] = phone
    context.user_data["__state__"] = TAXI_TYPE
    await update.message.reply_text("✅ Номер принят!", reply_markup=ReplyKeyboardRemove())

    reply_markup = ReplyKeyboardMarkup(
        [[KeyboardButton("Стандарт"), KeyboardButton("Эрмитаж")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text("🚕 Выберите тип такси:", reply_markup=reply_markup)
    return TAXI_TYPE


async def taxi_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected_type = update.message.text

    if selected_type not in TARIFFS:
        await update.message.reply_text("⚠ Выберите вариант из предложенных!")
        return TAXI_TYPE

    context.user_data["taxi_type"] = selected_type
    context.user_data["__state__"] = CONFIRM_TAXI_TYPE
    keyboard = [
        [InlineKeyboardButton("✅ Да", callback_data="confirm_yes")],
        [InlineKeyboardButton("❌ Нет", callback_data="confirm_no")],
    ]
    await update.message.reply_text(
        f"Подтверждаете выбор тарифа '{selected_type}'?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CONFIRM_TAXI_TYPE


async def confirm_taxi_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_yes":
        await query.message.delete()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="⌛ Рассчитываю ваш заказ...",
            reply_markup=ReplyKeyboardRemove(),
        )
        return await process_order(update, context)

    elif query.data == "confirm_no":
        await query.message.delete()
        reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton("Стандарт"), KeyboardButton("Эрмитаж")]],
            resize_keyboard=True,
        )
        await query.message.reply_text("🔄 Выберите тип такси:", reply_markup=reply_markup)
        context.user_data["__state__"] = TAXI_TYPE
        return TAXI_TYPE


async def back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        current_state = context.user_data.get("__state__")
        states_order = [START_ADDRESS, END_ADDRESS, PHONE_NUMBER, TAXI_TYPE, CONFIRM_TAXI_TYPE]

        if current_state is None:
            await query.message.reply_text("❌ Невозможно определить текущий шаг.")
            return ConversationHandler.END

        if current_state in states_order:
            index = states_order.index(current_state)
            if index > 0:
                prev_state = states_order[index - 1]
                context.user_data["__state__"] = prev_state

                if prev_state == START_ADDRESS:
                    context.user_data.pop("start_address", None)
                    await query.edit_message_text("📍 Введите начальный адрес:")
                elif prev_state == END_ADDRESS:
                    context.user_data.pop("end_address", None)
                    await query.edit_message_text("📍 Введите конечный адрес:")
                elif prev_state == PHONE_NUMBER:
                    context.user_data.pop("phone_number", None)
                    await query.edit_message_text("📱 Введите ваш телефон:")
                elif prev_state == TAXI_TYPE:
                    context.user_data.pop("taxi_type", None)
                    reply_markup = ReplyKeyboardMarkup(
                        [[KeyboardButton("Стандарт"), KeyboardButton("Эрмитаж")]],
                        resize_keyboard=True,
                    )
                    await query.message.reply_text("🚕 Выберите тип такси:", reply_markup=reply_markup)

                return prev_state

        await query.message.reply_text("❌ Невозможно вернуться назад.")
        return current_state

    except Exception as e:
        logger.error(f"Ошибка возврата: {str(e)}")
        await query.message.reply_text("⚠ Ошибка. Попробуйте снова.")
        return ConversationHandler.END


async def process_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data

    try:
        start_coords = get_coordinates(user_data["start_address"])
        end_coords = get_coordinates(user_data["end_address"])

        error_details = []
        if not start_coords:
            error_details.append("Начальный адрес: не найден или вне Воронежа.")
        if not end_coords:
            error_details.append("Конечный адрес: не найден или вне Воронежа.")

        if error_details:
            error_msg = "⚠ Проблемы с адресами!\n" + "\n".join(error_details)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=error_msg + "\n\n📍 Введите начальный адрес:",
            )
            return RESTART_ADDRESS

        distance = geodesic(start_coords, end_coords).kilometers
        cost = calculate_trip_cost(user_data["taxi_type"], distance)

        message = (
            f"🚖 *Заказ подтверждён! В течение 5 минут с вами свяжется оператор и подберёт для вас лучший автомобиль!*\n\n"
            f"▪ Откуда: {user_data['start_address']}\n"
            f"▪ Куда: {user_data['end_address']}\n"
            f"▪ Телефон: `{user_data['phone_number']}`\n"
            f"▪ Тариф: {user_data['taxi_type']}\n"
            f"▪ Расстояние(Указывается напрямую без учёта дорог): {distance:.1f} км\n"
            f"▪ Стоимость: {cost:.0f} ₽\n\n"
            f"[🗺 Маршрут]({get_yandex_map_link(start_coords, end_coords)})"
        )

        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=message, parse_mode="Markdown"
        )

        await context.bot.send_message(
            chat_id=TO_USER_ID, text=f"🔥 *Новый заказ!*\n{message}", parse_mode="Markdown"
        )

        keyboard = [
            [InlineKeyboardButton("✅ Да", callback_data="repeat_yes")],
            [InlineKeyboardButton("❌ Нет", callback_data="repeat_no")],
        ]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Хотите заказать ещё одно такси?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return REPEAT_ORDER

    except Exception as e:
        logger.error(f"Ошибка обработки заказа: {str(e)}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠ Ошибка обработки заказа. Попробуйте снова.",
        )
        return ConversationHandler.END


async def repeat_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "repeat_yes":
        context.user_data.clear()
        await query.message.delete()
        await query.message.reply_text(
            "📍 Введите начальный адрес:", reply_markup=ReplyKeyboardRemove()
        )
        return START_ADDRESS
    else:
        await query.message.delete()
        await query.message.reply_text(
            "Спасибо за использование нашего сервиса! Если хотите заказать ещё одно такси, снова введите /start 😊", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END


async def restart_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["start_address"] = update.message.text
    context.user_data["__state__"] = END_ADDRESS
    await update.message.reply_text("✅ Новый начальный адрес сохранен!\n📍 Введите конечный адрес:")
    return END_ADDRESS


# ====================== ЗАПУСК ======================
def main():
    persistence = PicklePersistence(filepath="conversationbot")
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(persistence)
        .build()
    )

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^call_taxi$")],
        states={
            START_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, start_address),
                CallbackQueryHandler(back_handler, pattern="^back$"),
            ],
            END_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, end_address),
                CallbackQueryHandler(back_handler, pattern="^back$"),
            ],
            PHONE_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, phone_number),
                CallbackQueryHandler(back_handler, pattern="^back$"),
            ],
            TAXI_TYPE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, taxi_type),
                CallbackQueryHandler(back_handler, pattern="^back$"),
            ],
            CONFIRM_TAXI_TYPE: [
                CallbackQueryHandler(confirm_taxi_type, pattern="^(confirm_yes|confirm_no)$"),
                CallbackQueryHandler(back_handler, pattern="^back$"),
            ],
            RESTART_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, restart_address),
                CallbackQueryHandler(back_handler, pattern="^back$"),
            ],
            REPEAT_ORDER: [
                CallbackQueryHandler(repeat_order, pattern="^(repeat_yes|repeat_no)$")
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        persistent=True,
        name="main_conversation",
        per_chat=True,  # Убрали per_message=True
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.run_polling()


if __name__ == "__main__":
    main()
