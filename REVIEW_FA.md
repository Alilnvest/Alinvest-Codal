# گزارش بررسی انتشار Alilnvest-Codal

## نتیجه

پروژه در نسخه‌ی اصلاحی **1.5.4** آماده‌ی build و انتشار است. هر دو artifact زیر با موفقیت ساخته شدند و `twine check` آن‌ها را تأیید کرد:

- `alilnvest_codal-1.5.4-py3-none-any.whl`
- `alilnvest_codal-1.5.4.tar.gz`

## مشکلات پیدا‌شده و اصلاح‌شده

1. در `pyproject.toml` مقدار `project.license` به شکل قدیمیِ TOML table یعنی `{ text = "MIT" }` بود. این مقدار به SPDX استاندارد یعنی `license = "MIT"` تبدیل شد.
2. classifier قدیمی `License :: OSI Approved :: MIT License` حذف شد؛ setuptools جدید آن را deprecated اعلام می‌کند.
3. فایل‌های مجوز `LICENSE` و `NOTICE.md` با `license-files` در metadata بسته ثبت شدند.
4. حداقل نسخه‌ی build backend به `setuptools>=77` افزایش یافت تا قالب جدید مجوز روی محیط build تضمین شود.
5. نسخه‌ی metadata و نسخه‌ی runtime قبلاً 1.5.3 بودند؛ برای انتشار اصلاحی به‌صورت هماهنگ به `1.5.4` تغییر کردند.
6. Ruff تعداد 26 خطای پایه پیدا کرد: عمدتاً importهای عمدی در `__init__.py` و چند دستور چندگانه در یک خط در `automation.py`. importهای سازگارکننده به‌صورت مستند مستثنا شدند و کد automation خواناتر شد.

## آزمون‌های انجام‌شده

- `python -m build --sdist --wheel`: موفق، بدون هشدار deprecated مربوط به مجوز
- `twine check dist/*`: موفق برای wheel و sdist
- `ruff check src`: موفق
- `python -m compileall -q src`: موفق
- import عمومی پکیج: موفق
- smoke test تبدیل سلول‌ها به DataFrame: موفق
- نسخه‌ی import‌شده: `1.5.4`

## نکته‌ی انتشار

نام distribution در PyPI برابر `Alilnvest-Codal` است، اما import پایتون همچنان `alilnvest_codal` باقی می‌ماند. قبل از upload نهایی، اگر نام distribution قبلاً در PyPI متعلق به شما نیست، باید نام دیگری انتخاب شود؛ PyPI اجازه‌ی جایگزینی مالکیت نام موجود را نمی‌دهد.

## انتشار

```bash
python -m pip install --upgrade build twine
python -m build --sdist --wheel
python -m twine check dist/*
python -m twine upload dist/*
```
