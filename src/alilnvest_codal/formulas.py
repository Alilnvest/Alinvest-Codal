"""بررسی‌های فرمولِ خودِ کدال روی داده‌ی خامِ یک صورت (بدونِ eval و بدونِ برچسب‌های فارسی).

کدال برای جمع‌ها و درصدها فرمول می‌فرستد؛ مثلاً `SUM(B4:B5)`، `B14+B16+B17`، `SUM(B2:K2)` (جمع در یک سطر)
یا `IF(B8*C8>0,ROUND((B8-C8)*100/ABS(C8),0),"--")`. اگر این فرمول‌ها را با مقدارِ ذخیره‌شده‌ی خانه‌ها
دوباره حساب کنیم، آزمونی داریم که به نام ردیف‌ها وابسته نیست.

پشتیبانی: مرجعِ سلول، بازه‌ی مستطیلی (مثل B2:K2 یا B4:B5)، + - * /، عدد، رشته‌ی دوگانه‌نقل‌قول،
مقایسه، و توابع SUM / ROUND / ABS / IF. هر چیز دیگری FormulaError می‌دهد و گزارش می‌شود.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<str>\"[^\"]*\")|(?P<ref>\$?[A-Z]{1,3}\$?\d+)"
    r"|(?P<func>[A-Z]+)\(|(?P<op><>|>=|<=|[-+*/(),:<>=]))"
)
_SUPPORTED_FUNCTIONS = ("SUM", "ROUND", "ABS", "IF")
_MAX_RANGE_CELLS = 100_000


class FormulaError(Exception):
    """فرمولی که این ارزیاب پشتیبانی نمی‌کند، یا نمی‌تواند با داده‌ی موجود حساب کند."""


def _tokens(text: str) -> list[tuple[str, str]]:
    out, pos, text = [], 0, text.strip()
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            raise FormulaError(f"نشانه‌ی ناشناخته از موقعیت {pos}: {text[pos:pos + 12]!r}")
        pos = match.end()
        for kind in ("num", "str", "ref", "func", "op"):
            if match.group(kind) is not None:
                out.append((kind, match.group(kind)))
                break
    return out


def _split_ref(ref: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", ref.replace("$", ""))
    if not match:
        raise FormulaError(f"مرجع نامعتبر {ref!r}")
    return match.group(1), int(match.group(2))


def _col_number(letters: str) -> int:
    number = 0
    for ch in letters:
        number = number * 26 + (ord(ch) - 64)
    return number


def _col_letters(number: int) -> str:
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def parse_formula(text: str) -> tuple:
    """فرمول را به درخت تبدیل می‌کند. درخت فقط از گره‌های شناخته‌شده تشکیل می‌شود."""
    toks = _tokens(text.lstrip("="))
    pos = [0]

    def peek() -> tuple[str | None, str | None]:
        return toks[pos[0]] if pos[0] < len(toks) else (None, None)

    def take(expected: str | None = None) -> tuple[str, str]:
        tok = peek()
        if expected is not None and tok[1] != expected:
            raise FormulaError(f"انتظار {expected!r} ولی {tok[1]!r}")
        pos[0] += 1
        return tok

    def comparison() -> tuple:
        left = expression()
        if peek()[1] in ("<>", ">=", "<=", ">", "<", "="):
            op = take()[1]
            return ("cmp", op, left, expression())
        return left

    def expression() -> tuple:
        node = term()
        while peek()[1] in ("+", "-"):
            op = take()[1]
            node = ("bin", op, node, term())
        return node

    def term() -> tuple:
        node = unary()
        while peek()[1] in ("*", "/"):
            op = take()[1]
            node = ("bin", op, node, unary())
        return node

    def unary() -> tuple:
        if peek()[1] in ("-", "+"):
            op = take()[1]
            return ("neg", unary()) if op == "-" else unary()
        return primary()

    def argument() -> tuple:
        if peek()[0] == "ref" and pos[0] + 1 < len(toks) and toks[pos[0] + 1][1] == ":":
            start = take()[1]
            take(":")
            end = take()[1]
            col1, row1 = _split_ref(start)
            col2, row2 = _split_ref(end)
            c1, c2 = sorted((_col_number(col1), _col_number(col2)))
            r1, r2 = sorted((row1, row2))
            if (c2 - c1 + 1) * (r2 - r1 + 1) > _MAX_RANGE_CELLS:
                raise FormulaError("بازه‌ی بیش از حد بزرگ است")
            return ("range", c1, r1, c2, r2)
        return comparison()

    def primary() -> tuple:
        kind, text_value = peek()
        if kind == "num":
            take()
            return ("num", float(text_value))
        if kind == "str":
            take()
            return ("str", text_value[1:-1])
        if kind == "ref":
            take()
            if peek()[1] == ":":
                raise FormulaError("بازه بیرون از SUM")
            col, row = _split_ref(text_value)
            return ("ref", f"{col}{row}")
        if kind == "func":
            take()
            args: list[tuple] = []
            if peek()[1] != ")":
                while True:
                    args.append(argument())
                    if peek()[1] == ",":
                        take()
                        continue
                    break
            take(")")
            if text_value not in _SUPPORTED_FUNCTIONS:
                raise FormulaError(f"تابع پشتیبانی‌نشده: {text_value}")
            return ("call", text_value, args)
        if text_value == "(":
            take("(")
            node = comparison()
            take(")")
            return node
        raise FormulaError(f"نشانه‌ی نامنتظر {text_value!r}")

    tree = comparison()
    if pos[0] != len(toks):
        raise FormulaError("باقی‌مانده‌ی نامنتظر در فرمول")
    return tree


def excel_round(value: float, digits: int = 0) -> float:
    """گرد کردن به سبک Excel: نیم‌ها از صفر دور می‌شوند (round() پایتون زوج‌گرد است)."""
    factor = 10.0 ** digits
    rounded = math.floor(abs(value) * factor + 0.5 + 1e-9) / factor
    return rounded if value >= 0 else -rounded


def _num(value: Any) -> float:
    if isinstance(value, str):
        raise FormulaError(f"عملیات عددی روی متن {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise FormulaError(f"مقدار عددی نامعتبر {value!r}") from exc
    if not math.isfinite(number):
        raise FormulaError(f"مقدار غیرمتناهی {value!r}")
    return number


def evaluate_tree(node: tuple, value_of: Callable[[str], Any]) -> Any:
    kind = node[0]
    if kind in ("num", "str"):
        return node[1]
    if kind == "ref":
        return value_of(node[1])
    if kind == "range":
        raise FormulaError("بازه فقط داخل SUM معنا دارد")
    if kind == "neg":
        return -_num(evaluate_tree(node[1], value_of))
    if kind == "bin":
        op = node[1]
        left = _num(evaluate_tree(node[2], value_of))
        right = _num(evaluate_tree(node[3], value_of))
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if right == 0:
            raise FormulaError("تقسیم بر صفر")
        return left / right
    if kind == "cmp":
        op = node[1]
        left, right = evaluate_tree(node[2], value_of), evaluate_tree(node[3], value_of)
        if isinstance(left, str) or isinstance(right, str):
            left, right = str(left), str(right)
        return {"<>": left != right, ">=": left >= right, "<=": left <= right,
                ">": left > right, "<": left < right, "=": left == right}[op]
    if kind == "call":
        name, args = node[1], node[2]
        if name == "IF":
            if len(args) not in (2, 3):
                raise FormulaError("IF دو یا سه آرگومان می‌خواهد")
            condition = evaluate_tree(args[0], value_of)
            if isinstance(condition, str):
                raise FormulaError("شرط IF متنی است")
            if condition:
                return evaluate_tree(args[1], value_of)
            return evaluate_tree(args[2], value_of) if len(args) == 3 else False  # IF تنبل است، مثل Excel
        if name == "SUM":
            total = 0.0
            for arg in args:
                if arg[0] == "range":
                    _, c1, r1, c2, r2 = arg
                    for col in range(c1, c2 + 1):
                        for row in range(r1, r2 + 1):
                            cell = value_of(f"{_col_letters(col)}{row}")
                            if not isinstance(cell, str):  # متن داخل بازه، مثل Excel، نادیده گرفته می‌شود
                                total += _num(cell)
                else:
                    total += _num(evaluate_tree(arg, value_of))
            return total
        if name == "ROUND":
            if len(args) != 2:
                raise FormulaError("ROUND دو آرگومان می‌خواهد")
            return excel_round(_num(evaluate_tree(args[0], value_of)), int(_num(evaluate_tree(args[1], value_of))))
        if name == "ABS":
            if len(args) != 1:
                raise FormulaError("ABS یک آرگومان می‌خواهد")
            return abs(_num(evaluate_tree(args[0], value_of)))
    raise FormulaError(f"گره‌ی ناشناخته {kind}")


def evaluate_formula(text: str, value_of: Callable[[str], Any]) -> Any:
    """فرمول را با مقدارهای داده‌شده حساب می‌کند. نتیجه عدد، رشته، یا False (IF بی‌شاخه‌ی else) است."""
    return evaluate_tree(parse_formula(text), value_of)


def _stored(value: Any) -> Any:
    """مقدارِ خامِ یک خانه: None یعنی خالی، «--» یعنی درصدِ نامعین، عدد، یا متن."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            return "__nonfinite__"
        return number
    text = str(value).strip()
    if text == "":
        return None
    if text == "--":
        return "--"
    try:
        number = float(text.replace(",", ""))
        return number if math.isfinite(number) else "__nonfinite__"
    except ValueError:
        return text


@dataclass
class FormulaReport:
    """نتیجه‌ی بررسیِ فرمول‌های یک صورت. checked = خانه‌هایی که حساب شدند؛ بقیه در unsupported می‌آیند."""

    checked: int = 0
    matched: int = 0
    undefined: int = 0
    mismatches: list[dict] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def verify_formulas(raw: dict, tolerance: float = 0.5) -> FormulaReport:
    """همه‌ی فرمول‌های یک datasource خام را با مقدارهای ذخیره‌شده‌ی خانه‌ها مقایسه می‌کند."""
    report = FormulaReport()
    for sheet in (raw or {}).get("sheets") or []:
        cells: list[dict] = []
        for table in sheet.get("tables") or []:
            cells.extend(table.get("cells") or [])
        by_address = {str(c.get("address")): c for c in cells if c.get("address")}

        def value_of(address: str) -> Any:
            cell = by_address.get(address)
            stored = _stored(cell.get("value")) if cell else None
            if stored is None:
                return 0.0  # خانه‌ی خالی در محاسبه صفر است، مثل Excel
            return stored

        for cell in cells:
            formula = cell.get("formula")
            if not formula:
                continue
            address = str(cell.get("address"))
            try:
                computed = evaluate_formula(str(formula), value_of)
                if isinstance(computed, (int, float)) and not math.isfinite(float(computed)):
                    raise FormulaError("نتیجه‌ی غیرمتناهی")
            except FormulaError as exc:
                report.unsupported.append({"address": address, "formula": formula, "reason": str(exc)})
                continue
            report.checked += 1
            stored = _stored(cell.get("value"))
            if computed is False or computed == "--":
                if stored in (None, "--") or stored == 0.0:
                    report.undefined += 1
                else:
                    report.mismatches.append({"address": address, "formula": formula,
                                              "computed": "--", "stored": stored})
                continue
            if isinstance(computed, str) or isinstance(stored, str):
                report.mismatches.append({"address": address, "formula": formula, "computed": computed, "stored": stored})
                continue
            expected = 0.0 if stored is None else stored
            if abs(computed - expected) <= tolerance:
                report.matched += 1
            else:
                report.mismatches.append({"address": address, "formula": formula, "computed": computed, "stored": stored})
    return report
