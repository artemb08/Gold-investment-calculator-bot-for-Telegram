import csv
import io
import json
import math
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Dict, Tuple

import requests
from bs4 import BeautifulSoup  # pip install beautifulsoup4

STOOQ_CSV_URL = "https://stooq.com/q/d/l/?s=xaueur&i=d"
INVESTING_URL = "https://www.investing.com/currencies/xau-eur-historical-data"
GRAMS_PER_OUNCE = 31.1034768

DATA_DIR = Path(".gold_plans_telega")
DATA_DIR.mkdir(exist_ok=True)


# ========= МОДЕЛИ =========

@dataclass
class PricePoint:
    date: date
    close: float  # EUR per ounce

@dataclass
class PlanRow:
    date: date
    price_per_gram_eur: float
    grams_for_budget: float

@dataclass
class ChildPlan:
    child_id: str
    name: str
    birth_date: date
    target_age_years: Optional[int]
    monthly_budget_eur: float
    plan_rows: List[PlanRow]

    def to_json(self) -> dict:
        return {
            "child_id": self.child_id,
            "name": self.name,
            "birth_date": self.birth_date.isoformat(),
            "target_age_years": self.target_age_years,
            "monthly_budget_eur": self.monthly_budget_eur,
            "plan_rows": [
                {
                    "date": r.date.isoformat(),
                    "price_per_gram_eur": r.price_per_gram_eur,
                    "grams_for_budget": r.grams_for_budget,
                }
                for r in self.plan_rows
            ],
        }

    @staticmethod
    def from_json(obj: dict) -> "ChildPlan":
        return ChildPlan(
            child_id=obj["child_id"],
            name=obj["name"],
            birth_date=datetime.strptime(obj["birth_date"], "%Y-%m-%d").date(),
            target_age_years=obj.get("target_age_years"),
            monthly_budget_eur=float(obj["monthly_budget_eur"]),
            plan_rows=[
                PlanRow(
                    date=datetime.strptime(r["date"], "%Y-%m-%d").date(),
                    price_per_gram_eur=float(r["price_per_gram_eur"]),
                    grams_for_budget=float(r["grams_for_budget"]),
                )
                for r in obj["plan_rows"]
            ],
        )


# ========= ЗАГРУЗКА ЦЕН =========

class PriceSourceError(Exception):
    pass

def get_user_plans_file(user_id: int) -> Path:
    """Возвращает путь к файлу планов конкретного пользователя."""
    return DATA_DIR / f"plans_user_{user_id}.json"

def download_stooq_xaueur() -> List[PricePoint]:
    try:
        resp = requests.get(STOOQ_CSV_URL, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        raise PriceSourceError(f"Stooq error: {e}")

    resp.encoding = "utf-8"
    text = resp.text
    f = io.StringIO(text)
    reader = csv.DictReader(f)
    rows: List[PricePoint] = []
    for row in reader:
        try:
            d = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            close = float(row["Close"])
        except Exception:
            continue
        rows.append(PricePoint(date=d, close=close))
    rows.sort(key=lambda r: r.date)
    if not rows:
        raise PriceSourceError("Stooq returned empty dataset.")
    return rows


def download_investing_xaueur() -> List[PricePoint]:
    """
    Очень простой fallback: парсит HTML-таблицу Investing.com.
    Структура сайта может поменяться, поэтому этот источник
    только как резервный.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; GoldPlanner/1.0)"
    }
    try:
        resp = requests.get(INVESTING_URL, headers=headers, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        raise PriceSourceError(f"Investing error: {e}")

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise PriceSourceError("Investing: historical table not found.")

    rows: List[PricePoint] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        try:
            # формат даты на сайте может отличаться, здесь пример DD.MM.YYYY
            d_text = tds[0].get_text(strip=True)
            d = datetime.strptime(d_text, "%d.%m.%Y").date()
            price_text = tds[1].get_text(strip=True).replace(",", "")
            close = float(price_text)
        except Exception:
            continue
        rows.append(PricePoint(date=d, close=close))
    rows.sort(key=lambda r: r.date)
    if not rows:
        raise PriceSourceError("Investing: parsed empty dataset.")
    return rows


def load_price_history() -> List[PricePoint]:
    """
    Пытается взять Stooq, при ошибке – Investing.
    """
    try:
        return download_stooq_xaueur()
    except PriceSourceError:
        return download_investing_xaueur()


# ========= УТИЛИТЫ ВРЕМЕНИ И ФИЛЬТРАЦИИ =========

def filter_period(points: List[PricePoint], start_date: date, end_date: date) -> List[PricePoint]:
    return [p for p in points if start_date <= p.date <= end_date]


def pick_monthly_dates(points: List[PricePoint], day_priority=None) -> List[PricePoint]:
    """
    Берём одну дату в месяц в порядке приоритета дней, по умолчанию 20→19→18→17→16.
    Важно: это приближение, пользователю нужно явно говорить, что дата
    может немного сдвигаться относительно выбранного числа.
    """
    if day_priority is None:
        day_priority = [20, 19, 18, 17, 16]

    by_month: Dict[Tuple[int, int], List[PricePoint]] = {}
    for p in points:
        key = (p.date.year, p.date.month)
        by_month.setdefault(key, []).append(p)

    picked: List[PricePoint] = []
    for (year, month), lst in by_month.items():
        best = None
        for d in day_priority:
            for p in lst:
                if p.date.day == d:
                    best = p
                    break
            if best is not None:
                break
        if best is None:
            # если нет ни одного из приоритетных дней – берем последнюю дату месяца
            best = max(lst, key=lambda x: x.date)
        picked.append(best)

    picked.sort(key=lambda p: p.date)
    return picked


def months_between_exact(d1: date, d2: date) -> int:
    """
    Более аккуратная оценка количества месяцев:
    считаем полные месяцы между датами.
    """
    if d2 <= d1:
        return 0
    years = d2.year - d1.year
    months = d2.month - d1.month
    total = years * 12 + months
    if d2.day < d1.day:
        total -= 1
    return max(total, 0)


# ========= РАСЧЁТ ПЛАНА =========

def build_plan_rows(points: List[PricePoint], monthly_budget_eur: float) -> List[PlanRow]:
    rows: List[PlanRow] = []
    for p in points:
        price_per_gram = p.close / GRAMS_PER_OUNCE
        grams = monthly_budget_eur / price_per_gram
        rows.append(
            PlanRow(
                date=p.date,
                price_per_gram_eur=price_per_gram,
                grams_for_budget=grams,
            )
        )
    return rows


def calc_year_stats(plan_rows: List[PlanRow]) -> Dict[int, float]:
    by_year: Dict[int, float] = {}
    for r in plan_rows:
        y = r.date.year
        by_year.setdefault(y, 0.0)
        by_year[y] += r.grams_for_budget
    return by_year


def average_monthly_return_with_target(plan_rows: List[PlanRow], target_months: int) -> float:
    """
    Консервативная оценка средней месячной доходности с учётом горизонта.
    target_months: количество месяцев до цели (например, 148 для 12 лет).
    """
    # 0. Базовый случай, когда данных мало
    if len(plan_rows) < 2:
        base_ret = 0.004  # 0.4% в месяц по умолчанию
        years = target_months / 12 if target_months > 0 else 10
        # Лёгкая корректировка под горизонт
        if years <= 5:
            return max(base_ret, 0.005)   # 0.5%
        elif years <= 10:
            return base_ret               # 0.4%
        elif years <= 20:
            return 0.0035                 # 0.35%
        else:
            return 0.003                  # 0.3%

    current_price = plan_rows[-1].price_per_gram_eur
    years = target_months / 12 if target_months > 0 else 10

    # 1. МАКСИМАЛЬНО ДОПУСТИМАЯ ЦЕНА В ЗАВИСИМОСТИ ОТ ГОРИЗОНТА
    #   0–10 лет  : максимум x2.0
    #   10–20 лет : максимум x3.0
    #   >20 лет   : максимум x3.5
    if years <= 10:
        max_price_target = current_price * 2.0
    elif years <= 20:
        max_price_target = current_price * 3.0
    else:
        max_price_target = current_price * 3.5

    if target_months > 0:
        max_allowed_return = (max_price_target / current_price) ** (1 / target_months) - 1
    else:
        max_allowed_return = 0.008  # запасной верх (0.8%), если горизонта нет

    # 2. ИСТОРИЧЕСКИЙ РОСТ (последние 5 лет)
    last_date = plan_rows[-1].date
    five_years_ago = date(last_date.year - 5, last_date.month, last_date.day)
    last_5y_rows = [r for r in plan_rows if r.date >= five_years_ago]

    if len(last_5y_rows) >= 12:
        hist_return = calculate_geometric_return(last_5y_rows)
    else:
        hist_return = 0.004  # 0.4% как базовая оценка

    # 3. КОРРЕКЦИЯ НА ВЫСОКИЙ УРОВЕНЬ ЦЕНЫ (мягко режем, но не слишком)
    if current_price > 90:
        # примерно -0.03% за каждые 10 EUR сверх 90
        price_penalty = max(0.0, (current_price - 90) / 10 * 0.0003)
        hist_return = max(0.0025, hist_return - price_penalty)

    # 4. БЕРЁМ МИНИМУМ ИЗ ИСТОРИЧЕСКОГО И "ПРЕДЕЛЬНО ДОПУСТИМОГО"
    final_return = min(hist_return, max_allowed_return)

    # 5. КОРИДОР В ЗАВИСИМОСТИ ОТ ГОРИЗОНТА (подогнан под твою таблицу)
    #   <=5 лет   : 0.5–0.8%  (для горизонта 5 лет цель ~175 EUR/г)
    #   5–10 лет  : 0.4–0.7%  (8 лет ~210 EUR/г)
    #   10–20 лет : 0.35–0.6% (12–20 лет ~260–370 EUR/г)
    #   >20 лет   : 0.3–0.55%
    if years <= 5:
        lo, hi = 0.0050, 0.0080
    elif years <= 10:
        lo, hi = 0.0040, 0.0070
    elif years <= 20:
        lo, hi = 0.0035, 0.0060
    else:
        lo, hi = 0.0030, 0.0055

    final_return = min(max(final_return, lo), hi)

    # 6. ВЫВОД ДЛЯ ПОЛЬЗОВАТЕЛЯ
    forecast_price_val = (
        current_price * ((1 + final_return) ** target_months)
        if target_months > 0
        else current_price
    )

    print(f"📊  Текущая цена: {current_price:.1f} EUR/г")
    annual_ret = (1 + final_return) ** 12 - 1
    print(f"📈  Рассчитанный рост: {final_return * 100:.3f}% (годовой: {annual_ret * 100:.1f}%)")
    print(f"🎯  До цели осталось: {target_months} мес. ({years:.1f} лет)")
    print(f"🔮  Прогнозная цена: {forecast_price_val:.0f} EUR/г")

    return final_return
def calculate_geometric_return(rows: List[PlanRow]) -> float:
    """Вычисляет геометрическую среднюю доходность цены по ряду плановых точек."""
    if len(rows) < 2:
        return 0.003  # 0.3% по умолчанию

    start_price = rows[0].price_per_gram_eur
    end_price = rows[-1].price_per_gram_eur
    months_count = max(months_between_exact(rows[0].date, rows[-1].date), 1)

    try:
        return (end_price / start_price) ** (1 / months_count) - 1
    except Exception:
        return 0.003
def forecast_price(last_price_per_gram: float, avg_monthly_ret: float, months_ahead: int) -> float:
    """
    Прогноз цены с одним усреднённым месячным ростом.
    На коротких горизонтах даёт результаты, похожие на таблицу:
    P_t = P_0 * (1 + r)^t, с мягким штрафом за очень высокую текущую цену.
    """
    if months_ahead <= 0:
        return last_price_per_gram

    price = last_price_per_gram
    effective_ret = avg_monthly_ret

    # Небольшой штраф, если текущая цена уже высокая
    if last_price_per_gram > 100.0:
        effective_ret *= 0.9  # -10% к среднему росту

    # Обычный экспоненциальный рост
    price = price * ((1.0 + effective_ret) ** months_ahead)

    # Глобальный потолок: не больше чем в 3.5 раза от текущей цены
    max_allowed = last_price_per_gram * 3.5
    if price > max_allowed:
        price = max_allowed

    return price



# ========= СОХРАНЕНИЕ ПЛАНОВ (НЕСКОЛЬКО ДЕТЕЙ) =========

def load_all_plans(user_id: int) -> Dict[str, ChildPlan]:
    """Загружает планы конкретного пользователя."""
    plans_file = get_user_plans_file(user_id)
    if not plans_file.exists():
        return {}
    try:
        raw = json.loads(plans_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    res: Dict[str, ChildPlan] = {}
    for child_id, obj in raw.items():
        try:
            res[child_id] = ChildPlan.from_json(obj)
        except Exception:
            continue
    return res


def save_all_plans(plans: Dict[str, ChildPlan], user_id: int) -> None:
    """Сохраняет планы конкретного пользователя."""
    plans_file = get_user_plans_file(user_id)
    raw = {cid: plan.to_json() for cid, plan in plans.items()}
    plans_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

def register_child(
    child_id: str,
    name: str,
    birth_date: date,
    target_age_years: Optional[int],
    monthly_budget_eur: float,
    price_points: List[PricePoint],
) -> ChildPlan:
    target_date: date
    if target_age_years is not None:
        target_date = date(birth_date.year + target_age_years, birth_date.month, birth_date.day)
    else:
        target_date = date.today()

    period_points = filter_period(price_points, birth_date, target_date)
    monthly_points = pick_monthly_dates(period_points)
    if len(monthly_points) < 6:
        # меньше 6 месяцев данных — предупреждение (на UI)
        pass

    plan_rows = build_plan_rows(monthly_points, monthly_budget_eur)

    return ChildPlan(
        child_id=child_id,
        name=name,
        birth_date=birth_date,
        target_age_years=target_age_years,
        monthly_budget_eur=monthly_budget_eur,
        plan_rows=plan_rows,
    )


def export_plan_to_csv(plan: ChildPlan, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "price_per_gram_eur", "grams_for_budget"])
        for r in plan.plan_rows:
            writer.writerow([r.date.isoformat(), f"{r.price_per_gram_eur:.4f}", f"{r.grams_for_budget:.4f}"])
