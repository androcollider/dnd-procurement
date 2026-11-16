"""ДНД Трейд: локальный MVP закупок. Только стандартная библиотека Python."""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import threading
import webbrowser
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("dnd")
MAX_BODY = 1_000_000


class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text(data, key, label, limit=200, required=False):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise AppError(f"{label}: требуется текст.")
    value = value.strip()
    if required and not value:
        raise AppError(f"Заполните поле «{label}».")
    if len(value) > limit:
        raise AppError(f"{label}: не более {limit} символов.")
    return value


def number(value, label, places=2, minimum=Decimal(0), maximum=Decimal("1000000000"), optional=False):
    if value is None or value == "":
        if optional:
            return None
        raise AppError(f"Укажите {label}.")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise AppError(f"{label}: требуется число.")
    try:
        n = Decimal(str(value).replace(",", "."))
        if not n.is_finite() or n < minimum or n > maximum:
            raise InvalidOperation
        quantum = Decimal(1).scaleb(-places)
        if n.quantize(quantum) != n:
            raise InvalidOperation
        return n
    except (InvalidOperation, ValueError):
        raise AppError(f"{label}: от {minimum} до {maximum}, знаков после запятой — не более {places}.")


def day(value, label, optional=False):
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str):
        raise AppError(f"{label}: неверная дата.")
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value or not 2000 <= result.year <= 2100:
            raise ValueError
        return value
    except ValueError:
        raise AppError(f"{label}: укажите дату от 2000 до 2100 года в формате ГГГГ-ММ-ДД.")


def purchase_values(data):
    kind = data.get("type")
    if kind not in ("material", "work"):
        raise AppError("Выберите тип закупки.")
    return {
        "name": text(data, "name", "Название закупки", 150, True),
        "type": kind,
        "quantity": str(number(data.get("quantity"), "количество", 3, Decimal("0.001"), Decimal("1000000"))),
        "unit": text(data, "unit", "Единица измерения", 15, True),
        "due": day(data.get("due"), "Требуемая дата"),
        "order_number": text(data, "order", "Номер заказа", 80),
        "requirements": text(data, "requirements", "Требования", 5000),
    }


def offer_values(data):
    match = data.get("match", "unknown")
    if match not in ("unknown", "yes", "deviation"):
        raise AppError("Выберите соответствие ТЗ.")
    values = {
        "company": text(data, "company", "Компания", 150, True),
        "payment": text(data, "payment", "Условия оплаты", 200),
        "ready": day(data.get("ready"), "Дата готовности", True),
        "match_status": match,
        "comment": text(data, "comment", "Комментарий", 3000),
    }
    for k, label in (("price", "Цена"), ("delivery", "Доставка"), ("extra", "Дополнительные расходы")):
        n = number(data.get(k), label, maximum=Decimal("10000000") if k == "price" else Decimal("1000000000"), optional=True)
        values[k + "_cents"] = None if n is None else int(n * 100)
    n = number(data.get("days"), "Дни доставки", 0, maximum=Decimal(3650), optional=True)
    values["days"] = None if n is None else int(n)
    return values


def calculate(p, o):
    parts = [o["price_cents"], o["delivery_cents"], o["extra_cents"]]
    total = None
    if all(v is not None for v in parts):
        amount = Decimal(p["quantity"]) * parts[0]
        total = int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP)) + parts[1] + parts[2]
    arrival = None
    if o["ready"] is not None and o["days"] is not None:
        arrival = (date.fromisoformat(o["ready"]) + timedelta(days=o["days"])).isoformat()
    risks = []
    if total is None:
        risks.append("Полная стоимость не уточнена")
    if arrival is None:
        risks.append("Срок получения не уточнён")
    elif arrival > p["due"]:
        risks.append("Получение позже требуемой даты")
    if o["match_status"] != "yes":
        risks.append("Есть отклонения от ТЗ" if o["match_status"] == "deviation" else "Соответствие ТЗ не проверено")
    return total, arrival, risks


SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
 type TEXT NOT NULL CHECK(type IN ('material','work')),
 quantity TEXT NOT NULL, unit TEXT NOT NULL, due TEXT NOT NULL,
 order_number TEXT NOT NULL DEFAULT '', requirements TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS offers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_id INTEGER NOT NULL REFERENCES purchases(id),
 company TEXT NOT NULL, price_cents INTEGER, delivery_cents INTEGER, extra_cents INTEGER,
 ready TEXT, days INTEGER, payment TEXT NOT NULL DEFAULT '',
 match_status TEXT NOT NULL CHECK(match_status IN ('unknown','yes','deviation')),
 comment TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS offers_purchase ON offers(purchase_id);
CREATE TABLE IF NOT EXISTS decisions (
 purchase_id INTEGER PRIMARY KEY REFERENCES purchases(id),
 offer_id INTEGER NOT NULL REFERENCES offers(id), reason TEXT NOT NULL,
 needs_review INTEGER NOT NULL DEFAULT 0, saved_at TEXT NOT NULL
);
PRAGMA user_version=1;
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Эта база создана более новой версией приложения.")
            c.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def require(c, table, identifier):
        if table not in ("purchases", "offers"):
            raise ValueError("Invalid table")
        row = c.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise AppError("Запись не найдена.", 404)
        return dict(row)

    @staticmethod
    def insert(c, table, values):
        columns = ",".join(values)
        return c.execute(f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in values)})", tuple(values.values())).lastrowid

    @staticmethod
    def update(c, table, identifier, values):
        c.execute(f"UPDATE {table} SET {','.join(k+'=?' for k in values)} WHERE id=?", (*values.values(), identifier))

    def detail(self, identifier, connection=None):
        if connection is None:
            with self.connect() as c:
                return self.detail(identifier, c)
        c = connection
        p = self.require(c, "purchases", identifier)
        offers = []
        for r in c.execute("SELECT * FROM offers WHERE purchase_id=? ORDER BY id", (identifier,)):
            o = dict(r)
            total, arrival, risks = calculate(p, o)
            o.update(total_cents=total, arrival=arrival, risks=risks)
            o["match"] = o.pop("match_status")
            for k in ("price", "delivery", "extra"):
                n = o[k + "_cents"]
                o[k] = "" if n is None else format(Decimal(n) / 100, ".2f")
            o["days"] = "" if o["days"] is None else str(o["days"])
            o["ready"] = o["ready"] or ""
            offers.append(o)
        p["order"] = p.pop("order_number")
        p["offers"] = offers
        d = c.execute("SELECT * FROM decisions WHERE purchase_id=?", (identifier,)).fetchone()
        p["decision"] = None if d is None else {
            "offerId": d["offer_id"], "reason": d["reason"], "needsReview": bool(d["needs_review"]), "savedAt": d["saved_at"]
        }
        return p

    def list(self):
        with self.connect() as c:
            return [self.detail(r[0], c) for r in c.execute("SELECT id FROM purchases ORDER BY id DESC").fetchall()]

    def save_purchase(self, data, identifier=None):
        values = purchase_values(data)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            stamp = now()
            if identifier is None:
                identifier = self.insert(c, "purchases", {**values, "created_at": stamp, "updated_at": stamp})
            else:
                old = self.require(c, "purchases", identifier)
                if data.get("revision") != old["revision"]:
                    raise AppError("Закупка изменена в другом окне. Откройте карточку заново перед редактированием.", 409)
                if (values["type"] != old["type"] or values["unit"] != old["unit"]) and c.execute("SELECT 1 FROM offers WHERE purchase_id=?", (identifier,)).fetchone():
                    raise AppError("При наличии предложений нельзя менять тип или единицу измерения. Создайте отдельную закупку.")
                if any(values[k] != old[k] for k in ("quantity", "due", "requirements")):
                    c.execute("UPDATE decisions SET needs_review=1 WHERE purchase_id=?", (identifier,))
                self.update(c, "purchases", identifier, {**values, "updated_at": stamp, "revision": old["revision"] + 1})
        return self.detail(identifier)

    def save_offer(self, purchase_id, data, identifier=None):
        values = offer_values(data)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self.require(c, "purchases", purchase_id)
            stamp = now()
            if identifier is None:
                identifier = self.insert(c, "offers", {**values, "purchase_id": purchase_id, "created_at": stamp, "updated_at": stamp})
            else:
                old = self.require(c, "offers", identifier)
                if old["purchase_id"] != purchase_id:
                    raise AppError("Предложение не относится к этой закупке.", 404)
                if data.get("revision") != old["revision"]:
                    raise AppError("Предложение изменено в другом окне. Откройте его заново.", 409)
                if any(values[k] != old[k] for k in values):
                    c.execute("UPDATE decisions SET needs_review=1 WHERE purchase_id=? AND offer_id=?", (purchase_id, identifier))
                self.update(c, "offers", identifier, {**values, "updated_at": stamp, "revision": old["revision"] + 1})
        return self.detail(purchase_id)

    def decide(self, purchase_id, data):
        reason = text(data, "reason", "Обоснование выбора", 2000, True)
        oid = data.get("offerId")
        if not isinstance(oid, int) or isinstance(oid, bool):
            raise AppError("Выберите исполнителя.")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            p = self.require(c, "purchases", purchase_id)
            o = self.require(c, "offers", oid)
            if o["purchase_id"] != purchase_id:
                raise AppError("Исполнитель должен относиться к текущей закупке.")
            if data.get("purchaseRevision") != p["revision"] or data.get("offerRevision") != o["revision"]:
                raise AppError("Условия изменились. Обновите сравнение и проверьте выбор ещё раз.", 409)
            _, _, risks = calculate(p, o)
            if risks and data.get("acceptRisks") is not True:
                raise AppError("Подтвердите, что учли предупреждения выбранного предложения.")
            c.execute("INSERT INTO decisions(purchase_id,offer_id,reason,needs_review,saved_at) VALUES(?,?,?,0,?) "
                      "ON CONFLICT(purchase_id) DO UPDATE SET offer_id=excluded.offer_id,reason=excluded.reason,needs_review=0,saved_at=excluded.saved_at",
                      (purchase_id, oid, reason, now()))
        return self.detail(purchase_id)

    def demo(self):
        with self.connect() as c:
            if c.execute("SELECT 1 FROM purchases LIMIT 1").fetchone():
                return False
        p = self.save_purchase({"name": "Ткань для партии спецодежды", "type": "material", "quantity": "1000", "unit": "м", "due": "2026-12-10", "order": "ЗК-2026-024", "requirements": "65% полиэстер / 35% хлопок; плотность 210 г/м²; ширина 150 см; цвет тёмно-синий. Учебные данные."})
        for company, price, delivery, ready, match, comment in [
            ("Текстиль-Снаб", "250", "5000", "2026-12-09", "yes", "Вымышленный поставщик, учебное предложение."),
            ("Вектор Текстиль", "258", "0", "2026-12-05", "yes", "Ткань в наличии, характеристики подтверждены."),
            ("ТканьПром", "254", "3000", "2026-12-06", "deviation", "Плотность 200 г/м² вместо 210 г/м².")]:
            self.save_offer(p["id"], {"company": company, "price": price, "delivery": delivery, "extra": "0", "ready": ready, "days": "3", "payment": "Предоплата 50%", "match": match, "comment": comment})
        return True


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DND-MVP/1.0"

        def log_message(self, message, *args):
            LOG.info(message, *args)

        def send(self, status, data, content_type="application/json; charset=utf-8"):
            raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8") if not isinstance(data, bytes) else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(raw)

        def body(self):
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                raise AppError("Используйте JSON.", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise AppError("Некорректный размер запроса.")
            if not 0 < length <= MAX_BODY:
                raise AppError("Неверный размер запроса.", 413)
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeDecodeError):
                raise AppError("Некорректный JSON.")
            if not isinstance(data, dict):
                raise AppError("Ожидается объект JSON.")
            return data

        def route(self):
            allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host", "")
            if host not in allowed_hosts:
                raise AppError("Разрешён только локальный адрес приложения.", 403)
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + h for h in allowed_hosts}:
                raise AppError("Запрос с другого сайта отклонён.", 403)
            path = urlparse(self.path).path
            method = self.command
            if method == "GET" and path in ("/", "/app.js", "/styles.css"):
                file = ROOT / "static" / ("index.html" if path == "/" else path[1:])
                mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[file.suffix]
                return self.send(200, file.read_bytes(), mime + "; charset=utf-8")
            if path == "/api/purchases":
                if method == "GET":
                    return self.send(200, store.list())
                if method == "POST":
                    return self.send(201, store.save_purchase(self.body()))
            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[:2] == ["api", "purchases"] and parts[2].isdigit():
                pid = int(parts[2])
                if len(parts) == 3:
                    if method == "GET":
                        return self.send(200, store.detail(pid))
                    if method == "PUT":
                        return self.send(200, store.save_purchase(self.body(), pid))
                if len(parts) == 4 and parts[3] == "offers" and method == "POST":
                    return self.send(201, store.save_offer(pid, self.body()))
                if len(parts) == 5 and parts[3] == "offers" and parts[4].isdigit() and method == "PUT":
                    return self.send(200, store.save_offer(pid, self.body(), int(parts[4])))
                if len(parts) == 4 and parts[3] == "decision" and method == "PUT":
                    return self.send(200, store.decide(pid, self.body()))
            raise AppError("Страница или операция не найдена.", 404)

        def handle_request(self):
            try:
                self.route()
            except AppError as e:
                self.send(e.status, {"error": str(e)})
            except sqlite3.OperationalError:
                LOG.exception("Database error")
                self.send(503, {"error": "База временно недоступна. Проверьте права на папку data и повторите запрос."})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                LOG.exception("Request failed")
                self.send(500, {"error": "Не удалось выполнить операцию. Подробности в окне сервера."})

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_request

    return Handler


def backup_database(database, target):
    source = Path(database)
    if not source.is_file():
        raise RuntimeError("База данных ещё не создана.")
    target = Path(target)
    if target.resolve() == source.resolve() or target.exists():
        raise RuntimeError("Укажите новый путь для резервной копии.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        with dst:
            src.backup(dst)


def main():
    parser = argparse.ArgumentParser(description="ДНД Трейд — локальное приложение закупок")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Добавить учебные примеры только в пустую базу")
    parser.add_argument("--backup", type=Path, help="Сделать резервную копию базы и завершиться")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    database = args.data_dir / "dnd.sqlite3"
    if args.backup:
        backup_database(database, args.backup)
        print("Резервная копия:", args.backup.resolve())
        return
    if not 1 <= args.port <= 65535:
        parser.error("Порт должен быть от 1 до 65535.")
    store = Store(database)
    if args.demo:
        store.demo()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(store))
    except OSError as e:
        raise SystemExit(f"Не удалось открыть порт {args.port}: {e}. Попробуйте --port 8766.")
    url = f"http://127.0.0.1:{args.port}"
    print(f"\nДНД Трейд: {url}\nБаза: {database.resolve()}\nДля остановки нажмите Ctrl+C.\n", flush=True)
    if not args.no_browser:
        threading.Timer(0.7, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nПриложение остановлено. Данные сохранены.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
