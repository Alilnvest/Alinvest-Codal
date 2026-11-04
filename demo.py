#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""دمو -- روی توسن (سودوزیان) و یه صندوق (پورتفوی) تست می‌کنه."""

import logging

import alilnvest_codal as cp

logging.basicConfig(level=logging.INFO)


def demo_income(symbol: str) -> None:
    print(f"\n=== سودوزیانِ {symbol} ===")
    reports = cp.income_statement(symbol)
    if not reports:
        print("چیزی پیدا نشد.")
        return
    r = reports[0]
    print(f"{len(reports)} گزارش -- آخرین دوره: {r.period_end_to_date} (حسابرسی‌شده={r.is_audited})")
    print(r.row_code_df)


def demo_portfolio(symbol: str) -> None:
    print(f"\n=== پورتفویِ {symbol} ===")
    reports = cp.fund_portfolio(symbol, header_row=1)
    if not reports:
        print("چیزی پیدا نشد (شاید header_row یا letter_type نیاز به تنظیم داره).")
        return
    r = reports[0]
    print(f"{len(reports)} گزارش -- آخرین دوره: {r.period_end_to_date}")
    print(r.grid_df.head(15))


if __name__ == "__main__":
    demo_income("توسن")
    demo_portfolio("دوایکس")
