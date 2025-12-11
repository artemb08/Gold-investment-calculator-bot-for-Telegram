# gold_telega.py
import logging
from datetime import date
from pathlib import Path

from telegram import (
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)



from gold_core_telega import (
    load_price_history,
    load_all_plans,
    save_all_plans,
    register_child,
    export_plan_to_csv,
    calc_year_stats,
    average_monthly_return_with_target,
    forecast_price,
    months_between_exact,
    PriceSourceError,
)
import os
from dotenv import load_dotenv
# ========= НАСТРОЙКИ =========

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
MAX_WEIGHT_GRAMS = 10000.0

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальные данные (как в CLI)
pricepoints = None
# Состояния для диалогов
(
    LANG_CHOOSE,
    MAIN_MENU,
    ADD_ID,
    ADD_NAME,
    ADD_BIRTH,
    ADD_TARGET,
    ADD_BUDGET,
    CHILD_MENU,
    CHILD_ACTION,
    CHILD_DEBT_HAVE,
    CHILD_DEBT_SPLIT,
    CHILD_DEBT_INCLUDE_BASE,
    CHILD_BUY_AHEAD_WEIGHT,
    CHILD_STATUS_HAVE,
) = range(14)


# ========= ВСПОМОГАТЕЛЬНОЕ =========

def get_lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("lang", "ru")


def label(context: ContextTypes.DEFAULT_TYPE, ru: str, en: str) -> str:
    return ru if get_lang(context) == "ru" else en


def format_main_menu(context: ContextTypes.DEFAULT_TYPE) -> str:
    return label(
        context,
        "============================\n"
        "Главное меню:\n"
        "  1) 👶 Добавить/обновить ребёнка\n"
        "  2) 👨‍👩‍👧 Список детей\n"
        "  3) 📂 Открыть ребёнка и расчёты\n"
        "  0) 🚪 Выход / завершить",
        "============================\n"
        "Main menu:\n"
        "  1) 👶 Add/update child\n"
        "  2) 👨‍👩‍👧 Show children\n"
        "  3) 📂 Open child & calculations\n"
        "  0) 🚪 Exit / finish",
    )


def format_child_menu(context: ContextTypes.DEFAULT_TYPE) -> str:
    return label(
        context,
        "----------------------------\n"
        "Меню ребёнка:\n"
        "  1) 📅 План по годам\n"
        "  2) 📊 Статус плана по месяцам ✅/❌\n"
        "  3) 💳 Долг / рассрочка\n"
        "  4) 🔮 Прогноз цены\n"
        "  5) 🛒 Покупка наперёд\n"
        "  6) 📄 Экспорт плана в CSV\n"
        "  0) ◀️ Назад в главное меню",
        "----------------------------\n"
        "Child menu:\n"
        "  1) 📅 Plan by years\n"
        "  2) 📊 Monthly plan status ✅/❌\n"
        "  3) 💳 Debt / installments\n"
        "  4) 🔮 Price forecast\n"
        "  5) 🛒 Buy ahead\n"
        "  6) 📄 Export plan to CSV\n"
        "  0) ◀️ Back to main menu",
    )


# ========= СТАРТ И ВЫБОР ЯЗЫКА =========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    global pricepoints
    user_id = update.effective_user.id

    # Загружаем планы пользователя в контекст
    if 'plans' not in context.user_data:
        context.user_data['plans'] = load_all_plans(user_id)
    context.user_data['user_id'] = user_id

    await update.message.reply_text(
        "Choose language / Выберите язык",
        reply_markup=ReplyKeyboardMarkup(
            [["Русский", "English"]],
            one_time_keyboard=True,
            resize_keyboard=True,
        ),
    )
    if pricepoints is None:
        await update.message.reply_text("Загружаю данные XAUEUR...")
        try:
            pricepoints = load_price_history()
            mindate = pricepoints[0].date
            maxdate = pricepoints[-1].date
            await update.message.reply_text(f"Данные доступны с {mindate} по {maxdate}.")
        except PriceSourceError as e:
            await update.message.reply_text(f"Ошибка источника данных: {e}")
            return ConversationHandler.END
    return LANG_CHOOSE


async def choose_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.lower()
    lang = "en" if ("eng" in text or "english" in text or text == "2") else "ru"
    context.user_data["lang"] = lang

    disclaimer = label(
        context,
        "⚠️ Важно: все расчёты являются приблизительной оценкой и НЕ являются инвестиционной рекомендацией.",
        "⚠️ Important: all calculations are rough estimates and NOT investment advice.",
    )
    await update.message.reply_text(disclaimer, reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text(
        format_main_menu(context),
        reply_markup=ReplyKeyboardMarkup(
            [["1", "2", "3"], ["0"]],
            resize_keyboard=True,
            one_time_keyboard=False,
        ),
    )
    return MAIN_MENU



# ========= ГЛАВНОЕ МЕНЮ =========

# ========= ГЛАВНОЕ МЕНЮ =========

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cmd = update.message.text.strip()
    plans = context.user_data.get('plans', {})

    if cmd == "0":
        await update.message.reply_text(
            label(context, "👋 Пока! Можешь вызвать /start, чтобы начать снова.", "👋 Bye! Use /start to begin again.")
        )
        return ConversationHandler.END
    elif cmd == "1":
        await update.message.reply_text(
            label(context, "🆔 Введи ID ребёнка (например 1):", "🆔 Enter child ID (e.g. 1):"),
            reply_markup=ReplyKeyboardRemove(),
        )
        return ADD_ID
    elif cmd == "2":
        if not plans:
            await update.message.reply_text(
                label(context, "Пока нет детей.", "No children yet."),
            )
        else:
            lines = []
            for cid, p in plans.items():
                if p.target_age_years is not None:
                    target = label(context, f"до {p.target_age_years} лет", "until age {age}").format(
                        age=p.target_age_years
                    )
                else:
                    target = label(context, "до сегодня", "until today")
                lines.append(f"{cid}: {p.name}, {target}, {p.monthly_budget_eur:.0f} EUR/мес")
            await update.message.reply_text("\n".join(lines))
        await update.message.reply_text(format_main_menu(context))
        return MAIN_MENU
    elif cmd == "3":
        await update.message.reply_text(
            label(context, "🆔 Введи ID ребёнка:", "🆔 Enter child ID:"),
            reply_markup=ReplyKeyboardRemove(),
        )
        return CHILD_MENU
    else:
        await update.message.reply_text(
            label(context, "Не понял команду. Нажми кнопку.", "Unknown command. Use the buttons."),
        )
        return MAIN_MENU


# ========= ДИАЛОГ ДОБАВЛЕНИЯ РЕБЁНКА =========

async def add_child_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["add_child_id"] = update.message.text.strip()
    await update.message.reply_text(label(context, "Имя ребёнка:", "Child name:"))
    return ADD_NAME


async def add_child_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["add_name"] = update.message.text.strip()
    await update.message.reply_text(
        label(context, "Дата рождения (YYYY-MM-DD):", "Birth date (YYYY-MM-DD):")
    )
    return ADD_BIRTH


async def add_child_birth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    s = update.message.text.strip()
    try:
        d = date.fromisoformat(s)
    except ValueError:
        await update.message.reply_text(
            label(context, "❌ Формат неверный. Введи YYYY-MM-DD:", "❌ Invalid format. Use YYYY-MM-DD:")
        )
        return ADD_BIRTH
    context.user_data["add_birth"] = d

    kb = [["16", "18", "21", "0"]]
    await update.message.reply_text(
        label(
            context,
            "До какого возраста покупать золото? 16/18/21, или 0 – до сегодня.",
            "Until what age to buy gold? 16/18/21, or 0 – until today.",
        ),
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True),
    )
    return ADD_TARGET


async def add_child_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    s = update.message.text.strip()
    if s == "0":
        target = None
    else:
        try:
            target = int(s)
        except ValueError:
            await update.message.reply_text(
                label(context, "Введи 16, 18, 21 или 0:", "Enter 16, 18, 21 or 0:")
            )
            return ADD_TARGET
    context.user_data["add_target_age"] = target
    await update.message.reply_text(
        label(
            context,
            "Месячный бюджет в EUR (например 255):",
            "Monthly budget in EUR (e.g. 255):",
        ),
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADD_BUDGET


async def add_child_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    global price_points
    s = update.message.text.strip()
    try:
        budget = float(s)
    except ValueError:
        await update.message.reply_text(
            label(context, "❌ Неверное число. Введи сумму в EUR:", "❌ Invalid number. Enter EUR amount:")
        )
        return ADD_BUDGET

    cid = context.user_data["add_child_id"]
    name = context.user_data["add_name"]
    birth = context.user_data["add_birth"]
    target_age = context.user_data["add_target_age"]
    user_id = context.user_data['user_id']

    plan = register_child(
        child_id=cid,
        name=name,
        birth_date=birth,
        target_age_years=target_age,
        monthly_budget_eur=budget,
        price_points=price_points,
    )

    # Сохраняем в контекст пользователя
    context.user_data['plans'][cid] = plan
    save_all_plans(context.user_data['plans'], user_id)

    await update.message.reply_text(
        label(
            context,
            f"✅ План для '{name}' сохранён. Месяцев в плане: {len(plan.plan_rows)}.",
            f"✅ Plan for '{name}' saved. Months in plan: {len(plan.plan_rows)}.",
        )
    )
    await update.message.reply_text(
        format_main_menu(context),
        reply_markup=ReplyKeyboardMarkup(
            [["1", "2", "3"], ["0"]],
            resize_keyboard=True,
        ),
    )
    return MAIN_MENU


# ========= МЕНЮ РЕБЁНКА =========

async def child_menu_enter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cid = update.message.text.strip()
    plans = context.user_data.get('plans', {})

    if cid not in plans:
        await update.message.reply_text(
            label(context, "❌ Нет такого ID. Вернись в главное меню и добавь ребёнка.",
                  "❌ No such ID. Go back to main menu and add a child."),
        )
        await update.message.reply_text(format_main_menu(context))
        return MAIN_MENU

    context.user_data["child_id"] = cid
    child = plans[cid]
    await update.message.reply_text(
        label(context, f"📂 Открыт ребёнок '{child.name}'.", f"📂 Child '{child.name}' opened.")
    )
    await update.message.reply_text(
        format_child_menu(context),
        reply_markup=ReplyKeyboardMarkup(
            [["1", "2"], ["3", "4"], ["5", "6"], ["0"]],
            resize_keyboard=True,
        ),
    )
    return CHILD_ACTION


async def child_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cmd = update.message.text.strip()
    cid = context.user_data.get("child_id")
    plans = context.user_data.get('plans', {})

    if not cid or cid not in plans:
        await update.message.reply_text(
            label(context, "❌ Сначала выбери ребёнка через главное меню.", "❌ Choose a child first from main menu."),
        )
        return MAIN_MENU

    child = plans[cid]
    plan_rows = child.plan_rows
    if not plan_rows:
        await update.message.reply_text(label(context, "План пуст.", "Plan is empty."))
        return CHILD_ACTION

    last_row = plan_rows[-1]
    last_price_per_gram = last_row.price_per_gram_eur

    if child.target_age_years is not None:
        target_date = date(
            child.birth_date.year + child.target_age_years,
            child.birth_date.month,
            child.birth_date.day,
        )
    else:
        target_date = date.today()

    months_total_to_target = months_between_exact(child.birth_date, target_date)
    months_fact = len(plan_rows)
    months_from_birth_to_last = months_between_exact(child.birth_date, plan_rows[-1].date)
    months_from_birth_to_last = max(months_from_birth_to_last, months_fact)
    remaining_months = max(0, months_total_to_target - months_from_birth_to_last)

    if cmd == "0":
        await update.message.reply_text(format_main_menu(context))
        return MAIN_MENU

    if cmd == "1":
        year_stats = calc_year_stats(plan_rows)
        lines = [label(context, "📅 План по годам (граммы):", "📅 Plan by years (grams):")]
        for y in sorted(year_stats):
            lines.append(f"{y}: {year_stats[y]:.4f} g")
        await update.message.reply_text("\n".join(lines))
        return CHILD_ACTION

    if cmd == "2":
        await update.message.reply_text(
            label(
                context,
                "💰 Сколько грамм золота уже есть (всего по этому ребёнку)?",
                "💰 How many grams of gold do you already have for this child?",
            )
        )
        return CHILD_STATUS_HAVE

    if cmd == "3":
        avg_ret = average_monthly_return_with_target(plan_rows, remaining_months)
        context.user_data["avg_ret"] = avg_ret
        context.user_data["last_price"] = last_price_per_gram
        context.user_data["months_fact"] = months_fact
        context.user_data["plan_rows"] = plan_rows

        await update.message.reply_text(
            label(
                context,
                "💰 Сколько грамм золота у тебя сейчас по этому ребёнку?",
                "💰 How many grams do you currently have for this child?",
            )
        )
        return CHILD_DEBT_HAVE

    if cmd == "4":
        if len(plan_rows) < 2:
            await update.message.reply_text(
                label(context, "Недостаточно точек для прогноза.", "Not enough points for forecast.")
            )
            return CHILD_ACTION

        avg_ret = average_monthly_return_with_target(plan_rows, remaining_months)
        msg_lines = [
            label(
                context,
                f"📈 Средний рост цены: {avg_ret * 100:.2f}%/мес (очень грубая оценка).",
                f"📈 Avg monthly price change: {avg_ret * 100:.2f}% (very rough).",
            )
        ]
        for m in [1, 3, 6, 12, 24]:
            fp = forecast_price(last_price_per_gram, avg_ret, m)
            msg_lines.append(
                label(
                    context,
                    f"  Через {m} мес: {fp:.2f} EUR/г",
                    f"  In {m} months: {fp:.2f} EUR/g",
                )
            )
        await update.message.reply_text("\n".join(msg_lines))
        await update.message.reply_text(
            label(
                context,
                "⏱ Введи кол-во месяцев для произвольного прогноза (или 0, чтобы пропустить):",
                "⏱ Enter number of months for custom forecast (or 0 to skip):",
            )
        )
        context.user_data["forecast_mode"] = True
        context.user_data["forecast_last_price"] = last_price_per_gram
        context.user_data["forecast_avg_ret"] = avg_ret
        return CHILD_ACTION

    if cmd == "5":
        context.user_data["plan_rows"] = plan_rows
        context.user_data["last_price"] = last_price_per_gram
        await update.message.reply_text(
            label(
                context,
                "⚖️ Сколько грамм хочешь купить сейчас по текущему курсу?",
                "⚖️ How many grams do you want to buy now at current price?",
            )
        )
        return CHILD_BUY_AHEAD_WEIGHT

    if cmd == "6":
        child = plans[cid]
        path = Path(f"{child.child_id}_plan.csv")
        export_plan_to_csv(child, path)
        with path.open("rb") as f:
            await update.message.reply_document(
                document=InputFile(f, filename=path.name),
                caption=label(
                    context,
                    "📄 План экспортирован в CSV.",
                    "📄 Plan exported to CSV.",
                ),
            )
        return CHILD_ACTION

    if context.user_data.get("forecast_mode"):
        s = cmd
        try:
            m = int(s)
        except ValueError:
            await update.message.reply_text(
                label(context, "Введи число месяцев или 0:", "Enter integer months or 0:")
            )
            return CHILD_ACTION
        if m > 0:
            fp = forecast_price(
                context.user_data["forecast_last_price"],
                context.user_data["forecast_avg_ret"],
                m,
            )
            await update.message.reply_text(
                label(
                    context,
                    f"🔮 Прогноз через {m} мес.: {fp:.2f} EUR/г",
                    f"🔮 Forecast in {m} months: {fp:.2f} EUR/g",
                )
            )
        context.user_data["forecast_mode"] = False
        return CHILD_ACTION

    await update.message.reply_text(
        label(context, "Не понял команду. Выбери пункт меню.", "Unknown command. Choose menu item."),
    )
    return CHILD_ACTION


# ========= СТАТУС ПЛАНА ПО МЕСЯЦАМ =========

async def child_status_have(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    s = update.message.text.strip()
    try:
        have_grams = float(s)
    except ValueError:
        await update.message.reply_text(
            label(context, "❌ Неверное число. Введи граммы:", "❌ Invalid number. Enter grams:")
        )
        return CHILD_STATUS_HAVE

    cid = context.user_data["child_id"]
    plans = context.user_data.get('plans', {})
    child = plans[cid]
    plan_rows = child.plan_rows

    grams_left = have_grams
    lines = [
        label(
            context,
            "📊 План по месяцам (дата, цена, граммы, статус):",
            "📊 Monthly plan (date, price, grams, status):",
        )
    ]
    for r in plan_rows:
        if grams_left >= r.grams_for_budget:
            status = "✅"
            grams_left -= r.grams_for_budget
        elif grams_left > 0:
            status = "✅❌"
            grams_left = 0.0
        else:
            status = "❌"
        lines.append(
            f"{r.date.isoformat()}, {r.price_per_gram_eur:.2f} EUR/g, {r.grams_for_budget:.4f} g, {status}"
        )
    await update.message.reply_text("\n".join(lines))
    await update.message.reply_text(format_child_menu(context))
    return CHILD_ACTION


# ========= ДОЛГ / РАССРОЧКА =========

async def child_debt_have(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    s = update.message.text.strip()
    try:
        have_grams = float(s)
    except ValueError:
        await update.message.reply_text(
            label(context, "❌ Неверное число. Введи граммы:", "❌ Invalid number. Enter grams:")
        )
        return CHILD_DEBT_HAVE

    plan_rows = context.user_data["plan_rows"]
    last_price_per_gram = context.user_data["last_price"]
    months_fact = context.user_data["months_fact"]
    avg_ret = context.user_data["avg_ret"]

    total_grams_plan = sum(r.grams_for_budget for r in plan_rows)

    if have_grams >= total_grams_plan:
        extra = have_grams - total_grams_plan
        extra_eur = extra * last_price_per_gram
        await update.message.reply_text(
            label(
                context,
                f"✅ План перекрыт. Избыток: {extra:.4f} г (~{extra_eur:.2f} EUR по текущей цене).",
                f"✅ Plan exceeded. Surplus: {extra:.4f} g (~{extra_eur:.2f} EUR at current price).",
            )
        )
        return CHILD_ACTION

    debt_grams = total_grams_plan - have_grams
    debt_eur_now = debt_grams * last_price_per_gram
    context.user_data["debt_grams"] = debt_grams

    await update.message.reply_text(
        label(
            context,
            f"📉 Не хватает {debt_grams:.4f} г (~{debt_eur_now:.2f} EUR по текущей цене).",
            f"📉 You miss {debt_grams:.4f} g (~{debt_eur_now:.2f} EUR at current price).",
        )
    )
    await update.message.reply_text(
        label(
            context,
            "📆 На сколько месяцев разделить долг? (например 3 или 6):",
            "📆 Over how many months to split the debt? (e.g. 3 or 6):",
        )
    )
    return CHILD_DEBT_SPLIT


async def child_debt_split(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    s = update.message.text.strip()
    try:
        n_months = int(s)
        if n_months <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            label(context, "❌ Некорректное число месяцев.", "❌ Invalid number of months.")
        )
        return CHILD_DEBT_SPLIT

    context.user_data["debt_n_months"] = n_months
    await update.message.reply_text(
        label(
            context,
            "➕ Учитывать базовый план (ежемесячный вес) в рассрочке? (да/нет):",
            "➕ Include base monthly weight in installments? (yes/no):",
        )
    )
    return CHILD_DEBT_INCLUDE_BASE


async def child_debt_include_base(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    s = update.message.text.strip().lower()
    include_base_plan = s in ("да", "yes", "y")
    context.user_data["debt_include_base"] = include_base_plan

    plan_rows = context.user_data["plan_rows"]
    last_price_per_gram = context.user_data["last_price"]
    months_fact = context.user_data["months_fact"]
    avg_ret = context.user_data["avg_ret"]
    debt_grams = context.user_data["debt_grams"]
    n_months = context.user_data["debt_n_months"]

    months_fact = len(plan_rows)
    total_grams_plan = sum(r.grams_for_budget for r in plan_rows)

    part_grams = debt_grams / n_months

    lines = []
    lines.append(
        label(
            context,
            f"📉 Общий долг: {debt_grams:.4f} г, делим на {n_months} месяцев ≈ {part_grams:.4f} г долга в месяц.",
            f"📉 Total debt: {debt_grams:.4f} g, split into {n_months} months ≈ {part_grams:.4f} g per month.",
        )
    )
    lines.append(
        label(
            context,
            "📈 Предполагаем рост цены по средней месячной доходности.\n",
            "📈 Assuming price growth according to average monthly return.\n",
        )
    )

    total_cost_installments = 0.0
    for i in range(1, n_months + 1):
        price_i = forecast_price(last_price_per_gram, avg_ret, i)
        grams_this_month = part_grams
        base_grams = 0.0
        if include_base_plan:
            avg_base_grams = total_grams_plan / months_fact
            grams_this_month += avg_base_grams
            base_grams = avg_base_grams

        cost_i = grams_this_month * price_i
        total_cost_installments += cost_i

        if get_lang(context) == "ru":
            line = (
                    f"Месяц {i}: цена ~{price_i:.2f} EUR/г, "
                    f"долг {part_grams:.4f} г"
                    + (f", базовый план {base_grams:.4f} г" if include_base_plan else "")
                    + f" → покупка {grams_this_month:.4f} г ≈ {cost_i:.2f} EUR"
            )
        else:
            line = (
                    f"Month {i}: price ~{price_i:.2f} EUR/g, "
                    f"debt {part_grams:.4f} g"
                    + (f", base plan {base_grams:.4f} g" if include_base_plan else "")
                    + f" → buy {grams_this_month:.4f} g ≈ {cost_i:.2f} EUR"
            )
        lines.append(line)

    cost_now_all_debt = debt_grams * last_price_per_gram
    diff = total_cost_installments - cost_now_all_debt

    lines.append(
        label(
            context,
            f"\n💸 Если закрыть весь долг ({debt_grams:.4f} г) СЕЙЧАС по {last_price_per_gram:.2f} EUR/г: "
            f"≈ {cost_now_all_debt:.2f} EUR.",
            f"\n💸 If you close the full debt ({debt_grams:.4f} g) NOW at {last_price_per_gram:.2f} EUR/g: "
            f"≈ {cost_now_all_debt:.2f} EUR.",
        )
    )
    lines.append(
        label(
            context,
            f"💳 Если тянуть рассрочку {n_months} мес (с учётом роста): ≈ {total_cost_installments:.2f} EUR.",
            f"💳 If you use installments for {n_months} months (with growth): ≈ {total_cost_installments:.2f} EUR.",
        )
    )
    if diff > 0:
        lines.append(
            label(
                context,
                f"⚠️ Рассрочка обойдётся дороже примерно на {diff:.2f} EUR из-за роста цены.",
                f"⚠️ Installments will cost about {diff:.2f} EUR more due to price growth.",
            )
        )
    else:
        lines.append(
            label(
                context,
                f"✅ При выбранных параметрах рассрочка выглядит выгоднее на {abs(diff):.2f} EUR (проверь допущения).",
                f"✅ With these assumptions, installments look cheaper by {abs(diff):.2f} EUR (check assumptions).",
            )
        )

    await update.message.reply_text("\n".join(lines))
    await update.message.reply_text(format_child_menu(context))
    return CHILD_ACTION


# ========= ПОКУПКА НАПЕРЁД =========

async def child_buy_ahead_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    s = update.message.text.strip()
    try:
        weight_now = float(s)
    except ValueError:
        await update.message.reply_text(
            label(context, "❌ Неверное число. Введи граммы:", "❌ Invalid number. Enter grams:")
        )
        return CHILD_BUY_AHEAD_WEIGHT

    plan_rows = context.user_data["plan_rows"]
    last_price_per_gram = context.user_data["last_price"]
    months_fact = len(plan_rows)

    price_now = last_price_per_gram
    cost_now = price_now * weight_now

    if not plan_rows:
        await update.message.reply_text(
            label(context, "⚠️ Нет плана для сравнения.", "⚠️ No plan to compare."),
        )
        return CHILD_ACTION

    total_plan_grams = [r.grams_for_budget for r in plan_rows]
    grams_left = weight_now
    months_covered = 0
    for g in total_plan_grams:
        if grams_left >= g:
            grams_left -= g
            months_covered += 1
        else:
            break

    avg_ret = average_monthly_return_with_target(plan_rows, months_fact)
    grams_to_simulate = weight_now
    month_index = 1
    cost_if_monthly = 0.0

    while grams_to_simulate > 1e-6 and month_index <= months_fact * 5:
        p_m = forecast_price(price_now, avg_ret, month_index)
        plan_g = plan_rows[min(month_index - 1, months_fact - 1)].grams_for_budget
        g_buy = min(plan_g, grams_to_simulate)
        cost_if_monthly += g_buy * p_m
        grams_to_simulate -= g_buy
        month_index += 1

    diff = cost_if_monthly - cost_now

    lines = []
    lines.append(
        label(
            context,
            f"🛒 Покупка {weight_now:.4f} г по текущей цене {price_now:.2f} EUR/г обойдётся ≈ {cost_now:.2f} EUR.",
            f"🛒 Buying {weight_now:.4f} g at current price {price_now:.2f} EUR/g will cost ≈ {cost_now:.2f} EUR.",
        )
    )
    lines.append(
        label(
            context,
            f"📦 При расходовании по плану это покрывает примерно {months_covered} месяцев.",
            f"📦 At planned pace this covers around {months_covered} months.",
        )
    )
    lines.append(
        label(
            context,
            f"⏱ Если покупать те же граммы постепенно по прогнозным ценам, стоимость была бы ≈ {cost_if_monthly:.2f} EUR.",
            f"⏱ If you bought the same grams gradually at forecast prices, cost would be ≈ {cost_if_monthly:.2f} EUR.",
        )
    )
    if diff > 0:
        lines.append(
            label(
                context,
                f"✅ Покупка сейчас экономит примерно {diff:.2f} EUR по сравнению с покупкой помесячно.",
                f"✅ Buying now saves about {diff:.2f} EUR vs monthly purchases.",
            )
        )
    else:
        lines.append(
            label(
                context,
                f"⚠️ Покупка сейчас обойдётся примерно на {abs(diff):.2f} EUR дороже, чем покупка помесячно.",
                f"⚠️ Buying now will cost about {abs(diff):.2f} EUR more than monthly purchases.",
            )
        )

    await update.message.reply_text("\n".join(lines))
    await update.message.reply_text(format_child_menu(context))
    return CHILD_ACTION


# ========= ОСНОВНОЙ LAUNCHER =========

def main() -> None:
    application = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LANG_CHOOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_lang)],
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, main_menu)],
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_child_id)],
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_child_name)],
            ADD_BIRTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_child_birth)],
            ADD_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_child_target)],
            ADD_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_child_budget)],
            CHILD_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, child_menu_enter)],
            CHILD_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, child_action)],
            CHILD_STATUS_HAVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, child_status_have)],
            CHILD_DEBT_HAVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, child_debt_have)],
            CHILD_DEBT_SPLIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, child_debt_split)],
            CHILD_DEBT_INCLUDE_BASE: [MessageHandler(filters.TEXT & ~filters.COMMAND, child_debt_include_base)],
            CHILD_BUY_AHEAD_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, child_buy_ahead_weight)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    application.add_handler(conv)
    application.run_polling()


if __name__ == "__main__":
    main()
