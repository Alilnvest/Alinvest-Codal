# راهنمای کامل کتابخانه `alilnvest_codal`

این سند مستندات نسخه‌ی اصلاح‌شده‌ی `alilnvest_codal` است؛ همان نسخه‌ای که مسیر جست‌وجو، دریافت datasource، دانلود Excel و تحلیل صورت‌های مالی آن روی داده‌ی واقعی کدال بررسی شده است.

> این کتابخانه برای دریافت و تحلیل اطلاعیه‌های کدال طراحی شده است. داده‌ها از endpointهای زنده‌ی کدال دریافت می‌شوند؛ بنابراین رفتار شبکه، rate limit و تغییر قالب اطلاعیه‌ها همچنان باید در برنامه‌ی مصرف‌کننده مدیریت شود.

---

## 1. نصب

### نصب در Linux، Termux یا محیط مجازی

در پوشه‌ای که `pyproject.toml` قرار دارد:

```bash
cd /path/to/alilnvest_codal-1.5.2-fixed
python -m pip install -e .
```

در Termux، اگر محیط مجازی فعال است:

```bash
cd /storage/emulated/0/Download/alilnvest_codal123
python -m pip install -e .
```

بررسی کنید Python از کدام نسخه استفاده می‌کند:

```bash
python - <<'PY'
import alilnvest_codal
print(alilnvest_codal.__file__)
print(alilnvest_codal.__version__)
PY
```

مسیر باید به پوشه‌ی پروژه یا محیط نصب‌شده‌ی جدید اشاره کند. اگر هنوز مسیر نسخه‌ی قدیمی در `site-packages` چاپ می‌شود، ابتدا آن را حذف کنید:

```bash
python -m pip uninstall -y alilnvest_codal
python -m pip install -e .
```

### وابستگی‌ها

وابستگی‌های اصلی:

- `requests` برای HTTP همگام؛
- `pandas` برای DataFrame و تحلیل؛
- `lxml` و `beautifulsoup4` برای parsing HTML/Excel-like؛
- `openpyxl` و `xlrd` برای موتورهای Excel؛
- `jdatetime` برای تاریخ پیش‌فرض جلالی؛
- `httpx` فقط برای مسیر async و کلاینت async، در صورت نصب extra مربوطه.

---

## 2. معماری کلی

جریان عمومی کتابخانه به این شکل است:

```text
symbol
  ↓
search.codal.ir
  ↓
Letter
  ↓
صفحه‌ی اطلاعیه در codal.ir
  ↓
var datasource یا ضمیمه Excel/HTML
  ↓
سلول‌های خام
  ↓
DataFrame
  ↓
FinancialReport / PortfolioReport
  ↓
تحلیل، مقایسه، ممیزی و ذخیره‌سازی
```

ماژول‌های اصلی:

| ماژول | مسئولیت |
|---|---|
| `search.py` | جست‌وجوی زنده‌ی اطلاعیه‌ها و pagination |
| `models.py` | مدل‌های `Letter`، `FinancialReport` و `PortfolioReport` |
| `datasource.py` | دریافت و استخراج `var datasource` از صفحه‌ی گزارش |
| `frames.py` | تبدیل سلول‌ها به DataFrame مالی یا جدول شبکه‌ای |
| `reports.py` | API سطح بالا برای صورت‌های مالی و پورتفوی |
| `excel.py` | دانلود و parsing ضمیمه‌های Excel/HTML |
| `sheets.py` | خواندن منوی شیت‌های گزارش و محتوای شیت |
| `http.py` | timeout، retry، rate limit، cache HTTP و circuit breaker |
| `cache.py` | cache حافظه و cache اختیاری روی دیسک |
| `store.py` | ذخیره‌ی پایدار datasource در SQLite |
| `analytics.py` | تمیزکاری جدول، مدل‌سازی و خروجی JSON |
| `checks.py` | کنترل‌های سازگاری و تحلیل صورت‌های مالی |
| `audit.py` | ممیزی کامل یک نماد یا چند نماد |
| `automation.py` | اجرای دوره‌ای و خروجی‌گیری خودکار |

---

## 3. مدل‌های داده

### 3.1. `Letter`

هر نتیجه‌ی جست‌وجوی کدال به‌صورت یک `Letter` برگردانده می‌شود. فیلدهای مهم:

```python
letter.tracing_no       # شناسه‌ی عددی اطلاعیه
letter.symbol            # نماد
letter.company_name     # نام شرکت
letter.title            # عنوان اطلاعیه
letter.letter_code      # کد رسمی اطلاعیه
letter.sent_date_time   # زمان ارسال
letter.publish_date_time# زمان انتشار
letter.has_html         # وجود صفحه HTML
letter.has_excel        # وجود فایل Excel
letter.has_pdf          # وجود PDF
letter.url              # لینک گزارش
letter.excel_url        # لینک Excel
letter.attachment_url   # لینک ضمیمه
letter.raw               # پاسخ خام کدال
```

نمونه:

```python
import alilnvest_codal as cp

letter = cp.latest_letter("خودرو", progress=False)
print(letter.tracing_no)
print(letter.title)
print(letter.url)
print(letter.excel_url)
```

### 3.2. `FinancialReport`

برای سود و زیان، ترازنامه، جریان نقدی، حقوق مالکانه و سود جامع استفاده می‌شود:

```python
report.tracing_no
report.symbol
report.company_name
report.title
report.sent_date_time
report.publish_date_time
report.is_audited
report.is_consolidated
report.period_end_to_date
report.year_end_to_date
report.row_code_df
report.raw
report.quality
```

`row_code_df` یک `pandas.DataFrame` است. ایندکس آن عموماً `row_code` است و برای ردیف‌های تکراری از `rowSequence` برای متمایزسازی استفاده می‌شود.

```python
print(report.row_code_df.shape)
print(report.row_code_df.head())
print(report.row_code_df.attrs.get("row_sequence"))
```

### 3.3. `PortfolioReport`

برای پورتفوی صندوق، شرکت‌های فرعی، شرکت‌های وابسته و اعضای هیئت‌مدیره استفاده می‌شود:

```python
portfolio.grid_df
portfolio.raw
portfolio.quality
portfolio.with_header()
portfolio.companies
portfolio.board
```

`grid_df` جدول شبکه‌ای است و برخلاف صورت مالی الزاماً `row_code` پایدار ندارد.

---

## 4. جست‌وجوی اطلاعیه‌ها

### 4.1. `search_letters`

امضای اصلی:

```python
search_letters(
    symbol: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    category: int = -1,
    letter_type: int = -1,
    audited: bool | None = None,
    max_pages: int | None = None,
    strict_symbol: bool = True,
    title_contains: str | None = None,
    letter_code: str | None = None,
    consolidated: bool | None = None,
    limit: int | None = None,
    progress: bool | callable | None = True,
    client: Any | None = None,
    use_cache: bool = True,
    raise_if_empty: bool = True,
) -> Iterator[Letter]
```

این تابع generator است؛ برای دریافت همه‌ی نتایج آن را به `list` تبدیل کنید:

```python
letters = list(cp.search_letters(
    "خودرو",
    from_date="1403/01/01",
    to_date="1403/12/30",
    limit=20,
    max_pages=3,
    progress=False,
    use_cache=True,
    raise_if_empty=False,
))
```

نکته‌ها:

- `limit=0` بدون درخواست شبکه خروجی خالی می‌دهد؛ مقدار منفی خطا است.
- `max_pages=0` بدون درخواست شبکه متوقف می‌شود؛ مقدار منفی خطا است.
- `from_date` و `to_date` تاریخ جلالی هستند.
- `raise_if_empty=False` برای ربات‌های batch مناسب است؛ در صورت نبود نتیجه exception نمی‌دهد.
- `strict_symbol=True` جلوی تطبیق نمادهای مشابه را می‌گیرد.

### 4.2. فیلترهای رایج

```python
letters = list(cp.search_letters(
    "فولاد",
    from_date="1403/01/01",
    to_date="1403/12/30",
    title_contains="صورت‌های مالی",
    consolidated=False,
    audited=False,
    limit=10,
    progress=False,
))
```

### 4.3. دریافت یک اطلاعیه با TracingNo

```python
letter = cp.get_letter(1612313)
print(letter.title)
```

---

## 5. تاریخ‌ها و بازه‌ی سال مالی

فرمت‌های معتبر:

```text
1403/01/01
1403-01-01
```

برای تحلیل دقیق سال ۱۴۰۳، هر دو مرز را تعیین کنید:

```python
FROM = "1403/01/01"
TO = "1403/12/30"

reports = cp.income_statement(
    "شراز",
    from_date=FROM,
    to_date=TO,
    use_cache=True,
    progress=False,
)
```

اگر فقط `from_date` را بدهید، جست‌وجو می‌تواند تا تاریخ امروز ادامه پیدا کند و اطلاعیه‌های بعد از سال ۱۴۰۳ را نیز بررسی کند.

اعتبارسنجی مستقل:

```python
cp.validate_jdate("1403/01/01")
```

تاریخ‌هایی مانند `1403/02/31` باید رد شوند.

---

## 6. انواع شیت در کدال

در کتابخانه دو خانواده‌ی اصلی شیت وجود دارد.

### 6.1. شیت‌های ساختاریافته‌ی صورت‌های مالی

این شیت‌ها داخل `var datasource` صفحه‌ی گزارش قرار دارند و به DataFrame با ایندکس `row_code` تبدیل می‌شوند.

| صورت | غیرتلفیقی | تلفیقی |
|---|---:|---:|
| صورت وضعیت مالی / ترازنامه | `0` | `14` |
| صورت سود و زیان | `1` | `13` |
| صورت جریان‌های نقدی | `9` | `15` |
| صورت سود و زیان جامع | `1058` | `1097` |
| صورت تغییرات در حقوق مالکانه | `1060` | `1099` |
| نظر حسابرس | `19` | معمولاً `19` |

کتابخانه برای صورت‌های اصلی بر اساس عنوان اطلاعیه، بین شناسه‌ی تلفیقی و غیرتلفیقی انتخاب می‌کند. بهتر است در استفاده‌ی عادی از توابع سطح بالا استفاده کنید و sheet ID را دستی ندهید.

### 6.2. شیت‌های ضمیمه‌ای و جدول‌محور

برخی محتواها در datasource نیستند یا ساختار row-code پایدار ندارند. این موارد از ضمیمه‌ی Excel/HTML خوانده می‌شوند:

- پورتفوی صندوق؛
- شرکت‌های فرعی؛
- شرکت‌های وابسته؛
- اعضای هیئت‌مدیره؛
- بعضی جدول‌های توضیحی.

برای این گروه، انتخاب اولین شیت غیرخالی قابل اعتماد نیست؛ نسخه‌ی اصلاح‌شده ابتدا نام و header مرتبط را بررسی می‌کند.

### 6.3. منوی شیت خود گزارش

برای دیدن شیت‌هایی که در صفحه‌ی گزارش اعلام شده‌اند:

```python
letter = cp.latest_letter("فولاد", progress=False)

options = cp.list_sheets(letter)
for option in options:
    print(option.sheet_id, option.title, option.alias)
```

خواندن محتوای یک شیت:

```python
content = cp.read_sheet(letter, option.sheet_id)

print(content.kind)   # datasource، html یا empty
print(content.title)
print(content.alias)
print(content.text)
print(content.tables)
```

`alias` همیشه معادل عنوان یا نوع واقعی محتوا نیست؛ برای تصمیم‌گیری، `title` و محتوای واقعی را نیز بررسی کنید.

---

## 7. صورت‌های مالی

### 7.1. سود و زیان

```python
reports = cp.income_statement(
    "خودرو",
    from_date="1403/01/01",
    to_date="1403/12/30",
    max_reports=None,
    use_cache=True,
    progress=False,
    max_concurrency=1,
)

for report in reports:
    print(report.period_end_to_date, report.row_code_df.shape)
```

### 7.2. ترازنامه

```python
reports = cp.balance_sheet(
    "خودرو",
    from_date="1403/01/01",
    to_date="1403/12/30",
    max_reports=None,
    use_cache=True,
    progress=False,
)
```

### 7.3. صورت جریان نقدی

```python
reports = cp.cash_flow_statement(
    "خودرو",
    from_date="1403/01/01",
    to_date="1403/12/30",
    max_reports=3,
    progress=False,
)
```

### 7.4. حقوق مالکانه و سود جامع

```python
equity = cp.equity_changes_statement(
    "خودرو",
    from_date="1403/01/01",
    to_date="1403/12/30",
    progress=False,
)

comprehensive = cp.comprehensive_income_statement(
    "خودرو",
    from_date="1403/01/01",
    to_date="1403/12/30",
    progress=False,
)
```

### 7.5. دریافت چند صورت در یک snapshot

برای جلوگیری از تکرار جست‌وجوی یک نماد، از `fetch_company_snapshot` استفاده کنید:

```python
snapshots = cp.fetch_company_snapshot(
    "شراز",
    from_date="1403/01/01",
    to_date="1403/12/30",
    max_reports=1,
    sheet_ids=None,
    progress=False,
)

for snapshot in snapshots:
    print(snapshot["tracing_no"])
    print(snapshot["income_statement"])
    print(snapshot["balance_sheet"])
    print(snapshot["cash_flow_statement"])
```

برای هر پنج صورت اصلی:

```python
snapshots = cp.fetch_company_snapshot(
    "شراز",
    sheet_ids="full",
    max_reports=1,
    progress=False,
)
```

در صورت نبود یک شیت، مقدار آن کلید می‌تواند `None` باشد. در تحلیل production، وضعیت ناقص بودن snapshot را در منطق برنامه‌ی خود ثبت کنید.

---

## 8. دانلود مستقیم Excel

### 8.1. دانلود Excel یک `Letter`

وقتی یک `Letter` مشخص دارید، تابع صحیح این است:

```python
from alilnvest_codal.excel import fetch_attachment_tables

letter = cp.get_letter(1612313)
sheets = fetch_attachment_tables(letter, use_cache=False)

for name, df in sheets.items():
    print(name, df.shape)
```

خروجی یک دیکشنری است:

```python
dict[str, pandas.DataFrame]
```

این تابع مستقیماً همان ضمیمه‌ی اطلاعیه را دانلود و parse می‌کند.

### 8.2. تفاوت با `fetch_excel_sheet`

`fetch_excel_sheet` برای جست‌وجوی اطلاعیه‌های یک **نماد** و پیدا کردن شیت مشخص در ضمیمه‌های آن است:

```python
reports = cp.fetch_excel_sheet(
    "خودرو",
    sheet_name="صورت وضعیت مالی",
    from_date="1403/01/01",
    to_date="1403/12/30",
    max_reports=1,
    max_scan=8,
    progress=False,
    use_cache=True,
)
```

این استفاده اشتباه است:

```python
cp.fetch_excel_sheet(letter, sheet_name=None)
```

زیرا آرگومان اول باید `str` نماد باشد و `sheet_name` نیز باید نام واقعی شیت باشد.

### 8.3. پورتفوی صندوق

```python
portfolio = cp.fund_portfolio(
    "دوایکس",
    from_date="1403/01/01",
    to_date="1403/12/30",
    max_reports=3,
    header_row=1,
    progress=False,
    use_cache=True,
)

for report in portfolio:
    print(report.period_end_to_date)
    print(report.grid_df.shape)
    print(report.grid_df.head())
```

پورتفوی معمولاً از Excel خوانده می‌شود، نه از `var datasource`.

---

## 9. شرکت‌های فرعی، وابسته و هیئت‌مدیره

این داده‌ها معمولاً در ضمیمه‌ی Excel هستند:

```python
subsidiaries = cp.subsidiary_companies(
    "خودرو",
    from_date="1403/01/01",
    to_date="1403/12/30",
    max_reports=1,
    progress=False,
)

for report in subsidiaries:
    report.with_header()
    for company in report.companies:
        print(company.name, company.registered_symbol)
```

برای شرکت‌های وابسته:

```python
affiliates = cp.affiliated_companies(
    "خودرو",
    max_reports=1,
    progress=False,
)
```

برای اعضای هیئت‌مدیره:

```python
board_reports = cp.board_members(
    "خودرو",
    max_reports=1,
    progress=False,
)

for report in board_reports:
    report.with_header()
    for member in report.board:
        print(member.name, member.role)
```

توجه: نبودن شرکت فرعی در خروجی الزماً خطا نیست؛ ممکن است شرکت واقعاً چنین داده‌ای در اطلاعیه منتشر نکرده باشد.

---

## 10. تبدیل سلول خام به DataFrame

### 10.1. `cells_to_dataframe`

برای صورت‌های مالی:

```python
from alilnvest_codal.frames import cells_to_dataframe

df = cells_to_dataframe(cells)
```

ویژگی‌ها:

- ایندکس بر اساس `row_code`؛
- جداسازی row codeهای تکراری با `rowSequence`؛
- نرمال‌سازی رشته‌ای و عددی `rowCode` و `columnSequence`؛
- حذف ردیف‌های کاملاً خالی با `drop_empty_rows=True`؛
- نگهداری mapping توالی در `df.attrs["row_sequence"]`.

### 10.2. `cells_to_grid`

برای جدول‌های لیستی:

```python
from alilnvest_codal.frames import cells_to_grid

df = cells_to_grid(cells, header_row=1)
```

اگر header ندارید:

```python
df = cells_to_grid(cells, header_row=None)
```

در صورت وجود headerهای تکراری، نسخه‌ی اصلاح‌شده نام‌ها را با suffix پایدار یکتا می‌کند.

### 10.3. نگاشت row code

```python
from alilnvest_codal.frames import apply_row_code_map

mapping = {
    1: "revenue",
    2: "cost_of_sales",
    3: "gross_profit",
}

named = apply_row_code_map(report.row_code_df, mapping)
```

ردیف‌هایی که در mapping نیستند حذف می‌شوند.

---

## 11. تحلیل و کنترل داده

### 11.1. کنترل یک صورت

```python
from alilnvest_codal import analyze_statement

result = analyze_statement(report.row_code_df)
print(result)
```

### 11.2. کنترل مجموعه صورت‌ها

```python
from alilnvest_codal import analyze_set

result = analyze_set(
    income=income_report.row_code_df,
    balance=balance_report.row_code_df,
    cash_flow=cash_report.row_code_df,
)
```

نام دقیق keywordها ممکن است بر اساس نسخه‌ی parser مورد استفاده متفاوت باشد؛ امضای تابع را با `inspect.signature` بررسی کنید.

### 11.3. ممیزی نماد

```python
from alilnvest_codal import audit_symbol

audit = audit_symbol(
    "شراز",
    kind="company",
    full=False,
    prior_original=True,
    compare_amendment=False,
    progress=False,
)

print(audit.to_dict())
```

ممیزی چند نماد:

```python
from alilnvest_codal import audit_symbols

results = audit_symbols(
    ["شراز", "خودرو", "فولاد"],
    progress=False,
)
```

در batch بزرگ بهتر است نمادها ترتیبی و با فاصله‌ی مؤدبانه پردازش شوند؛ خطای شبکه یک نماد می‌تواند نشانه‌ی block یا ناپایداری کدال باشد.

---

## 12. خروجی فایل

### خروجی JSON صورت مالی

```python
report.to_json(
    "income_1403.json",
    indent=2,
    ensure_ascii=False,
)
```

### خروجی Excel

```python
report.to_excel("income_1403.xlsx", index=True)
```

### خروجی CSV

```python
report.to_csv("income_1403.csv")
```

برای `PortfolioReport` نیز همین متدها وجود دارد:

```python
portfolio_report.to_json("portfolio.json")
portfolio_report.to_excel("portfolio.xlsx")
portfolio_report.to_csv("portfolio.csv")
```

نسخه‌ی اصلاح‌شده مقدارهای `NaN` و `Inf` را هنگام JSON به `null` تبدیل می‌کند.

---

## 13. cache و استفاده از داده‌های ذخیره‌شده

### 13.1. cache HTTP

در حالت عادی:

```python
reports = cp.income_statement(
    "شراز",
    from_date="1403/01/01",
    to_date="1403/12/30",
    use_cache=True,
    progress=False,
)
```

خاموش‌کردن cache برای تست واقعی شبکه:

```python
reports = cp.income_statement(
    "شراز",
    use_cache=False,
    progress=False,
)
```

پاک‌کردن cache:

```python
cp.clear_cache()
```

نکات مهم:

- cache صفحه‌ی گزارش با cache جست‌وجو یکسان نیست؛
- جست‌وجوی اطلاعیه‌های جدید TTL کوتاه‌تری دارد؛
- اگر مسیر cache در اجرای قبلی و فعلی فرق کند، hit اتفاق نمی‌افتد؛
- تغییر پارامترها، headerهای مؤثر یا URL باعث تغییر کلید cache می‌شود؛
- `use_cache=True` تضمین نمی‌کند خود فهرست اطلاعیه‌ها هرگز از شبکه خوانده نشود؛ برای اطلاعیه‌های جدید معمولاً جست‌وجو باید آنلاین بررسی شود.

### 13.2. SQLiteStore

برای نگهداری دائمی datasource:

```python
from alilnvest_codal import SQLiteStore

with SQLiteStore("./data/codal.sqlite3") as store:
    reports = cp.fetch_financial_reports(
        "شراز",
        sheet_id="1",
        from_date="1403/01/01",
        to_date="1403/12/30",
        store=store,
        use_cache=True,
        progress=False,
    )
```

بررسی شمارنده:

```python
with SQLiteStore("./data/codal.sqlite3") as store:
    print(store.count())
    print(store.count("ok"))
    print(store.count("absent"))
```

در نسخه‌ی اصلاح‌شده، busy timeout، upsert و مدیریت payload خراب بهبود یافته است؛ با این حال برای چند process مستقل، بهتر است اجرای نوشتن‌ها محدود یا ترتیبی شود.

---

## 14. کنترل سرعت، retry و circuit breaker

### تنظیم سرعت درخواست:

```python
cp.configure(
    min_delay=1.5,
    max_delay=2.5,
    max_retries=2,
    symbol_min_delay=15,
    symbol_max_delay=20,
)
```

در حالت محافظه‌کارانه:

```python
cp.safe_mode()
```

برای آزمایش کنترل‌شده‌ی یک نماد:

```python
cp.configure(
    max_retries=1,
    timeout=30,
    symbol_min_delay=0,
    symbol_max_delay=0,
)
```

پس از خطاهای پیاپی:

```python
cp.reset_circuit_breaker()
```

نکته: retry برای خطاهای گذرا مفید است، اما اگر کدال 429 یا WAF بدهد، زیادکردن concurrency می‌تواند مشکل را شدیدتر کند.

---

## 15. مدیریت خطا

استثناهای اصلی:

```python
from alilnvest_codal import (
    CodalError,
    SymbolNotFoundError,
    DataSourceError,
    NetworkError,
    RateLimitError,
    ExcelParseError,
)
```

الگوی پیشنهادی:

```python
try:
    reports = cp.income_statement(
        "شراز",
        from_date="1403/01/01",
        to_date="1403/12/30",
        progress=False,
    )
except RateLimitError as exc:
    print("کدال rate limit داده است:", exc.retry_after)
except SymbolNotFoundError as exc:
    print("نماد پیدا نشد:", exc.symbol)
except ExcelParseError as exc:
    print("Excel قابل خواندن نیست:", exc.url)
except DataSourceError as exc:
    print("datasource موجود نیست:", exc.tracing_no)
except NetworkError as exc:
    print("خطای شبکه:", exc.url, exc.status_code)
except CodalError as exc:
    print("خطای عمومی کتابخانه:", exc)
```

`RateLimitError` را قبل از `NetworkError` بگیرید، چون زیرکلاس خطای شبکه است.

---

## 16. استفاده در ربات بزرگ بازار

برای batch بزرگ، بازه را محدود کنید و از cache استفاده کنید:

```python
SYMBOLS = ["شراز", "خودرو", "فولاد"]
FROM = "1403/01/01"
TO = "1403/12/30"

for symbol in SYMBOLS:
    try:
        reports = cp.income_statement(
            symbol,
            from_date=FROM,
            to_date=TO,
            use_cache=True,
            progress=False,
            max_concurrency=1,
        )
        print(symbol, len(reports))
    except cp.RateLimitError:
        print("rate limit؛ batch متوقف شد")
        break
    except cp.NetworkError as exc:
        print(symbol, "خطای شبکه", exc)
```

برای رباتی که همه‌ی گزارش‌ها قبلاً cache شده‌اند، بررسی کنید:

```text
use_cache=True
مسیر cache همان مسیر قبلی
from_date/to_date همان پارامترهای ذخیره‌شده
cache قبل از اجرا پاک نمی‌شود
```

پیام «گزارش گمشده» معمولاً یعنی آن گزارش در cache یا store فعلی پیدا نشده است؛ این الزاماً به معنی نبودن گزارش در کدال نیست.

---

## 17. عیب‌یابی Termux و Android

### نمایش مسیر واقعی کتابخانه

```bash
python - <<'PY'
import alilnvest_codal
print(alilnvest_codal.__file__)
PY
```

### اجرای بدون buffer شدن خروجی

```bash
python -u bot.py
```

### گرفتن traceback هنگام گیرکردن

```bash
python -u -X faulthandler bot.py
```

اگر برنامه چند دقیقه روی یک heartbeat ثابت ماند، `Ctrl+C` بزنید و traceback کامل را ذخیره کنید. محل توقف معمولاً مشخص می‌کند که برنامه در HTTP، sleep، retry، parser یا SQLite مانده است.

### تست پایه‌ی آنلاین

```python
import alilnvest_codal as cp

letters = list(cp.search_letters(
    "خودرو",
    limit=3,
    progress=False,
    use_cache=False,
    raise_if_empty=False,
))
print(len(letters))

reports = cp.income_statement(
    "خودرو",
    max_reports=1,
    progress=False,
    use_cache=False,
)
print(reports[0].row_code_df.shape)
```

### تست مستقیم Excel

```python
from alilnvest_codal.excel import fetch_attachment_tables

letter = cp.get_letter(1612313)
sheets = fetch_attachment_tables(letter, use_cache=False)
print(list(sheets))
```

---

## 18. تفاوت توابع مهم

| تابع | ورودی اصلی | خروجی | کاربرد |
|---|---|---|---|
| `search_letters` | `symbol: str` | iterator از `Letter` | جست‌وجوی خام |
| `get_letter` | `tracing_no: int` | `Letter` | دریافت یک اطلاعیه |
| `get_datasource` | `Letter` | `dict` | دریافت JSON ساختاریافته |
| `fetch_attachment_tables` | `Letter` | `dict[str, DataFrame]` | دانلود مستقیم Excel/ضمیمه |
| `fetch_excel_sheet` | `symbol: str`, `sheet_name: str` | `list[PortfolioReport]` | جست‌وجوی نماد و پیدا کردن شیت مشخص |
| `income_statement` | `symbol: str` | `list[FinancialReport]` | سود و زیان |
| `balance_sheet` | `symbol: str` | `list[FinancialReport]` | ترازنامه |
| `fund_portfolio` | `symbol: str` | `list[PortfolioReport]` | پورتفوی صندوق |
| `fetch_company_snapshot` | `symbol: str` | `list[dict]` | دریافت چند صورت برای یک اطلاعیه |
| `cells_to_dataframe` | `list[dict]` | `DataFrame` | تبدیل سلول مالی |
| `cells_to_grid` | `list[dict]` | `DataFrame` | تبدیل جدول لیستی |

مهم‌ترین اشتباه استفاده‌ای که باید از آن جلوگیری شود:

```python
# نادرست
fetch_excel_sheet(letter, sheet_name=None)

# درست برای Letter موجود
fetch_attachment_tables(letter)

# درست برای جست‌وجوی نماد و شیت مشخص
fetch_excel_sheet("خودرو", sheet_name="صورت وضعیت مالی")
```

---

## 19. محدودیت‌های شناخته‌شده

1. فرمول‌هایی که به شیت دیگری ارجاع می‌دهند در grammar داخلی فرمول‌ها به‌صورت کامل پشتیبانی نمی‌شوند؛ فرمول‌های همان شیت قابل اعتمادترند.
2. قالب Excel کدال ممکن است برای ناشران یا سال‌های مختلف تفاوت داشته باشد.
3. نبود datasource یا نبود شیت خاص همیشه bug نیست؛ ممکن است اطلاعیه واقعاً آن بخش را نداشته باشد.
4. هیچ نرخ درخواستی نمی‌تواند تضمین کند IP هرگز block نشود؛ سیاست کدال ممکن است تغییر کند.
5. cache جست‌وجو با cache صفحه‌ی گزارش یکی نیست.
6. برای صحت تحلیل، `from_date` و `to_date` را صریح بدهید.
7. هنگام اجرای بازار بزرگ، concurrency پایین و cache روشن مناسب‌تر از درخواست‌های موازی زیاد است.
8. تست واقعی 429، 403، 502 و 503 به پاسخ زنده‌ی کدال وابسته است و باید در محیط مصرف‌کننده بررسی شود.

---

## 20. چک‌لیست استفاده‌ی production

قبل از اجرای batch بزرگ:

- مسیر `alilnvest_codal.__file__` را بررسی کنید؛
- نسخه‌ی کتابخانه را چاپ کنید؛
- `from_date` و `to_date` را مشخص کنید؛
- `use_cache=True` را فعال کنید؛
- مطمئن شوید cache مسیر ثابت دارد؛
- `max_concurrency=1` را برای اولین اجرای واقعی بگذارید؛
- `progress=False` را در سرویس‌های غیرتعاملی بگذارید؛
- خطاهای `RateLimitError` و `NetworkError` را جداگانه ثبت کنید؛
- برای هر گزارش `tracing_no` را در log بنویسید؛
- هنگام توقف طولانی با `faulthandler` traceback بگیرید؛
- پس از کامل‌شدن دانلود سال، اجرای بعدی را با cache بررسی کنید.

نمونه‌ی لاگ مناسب:

```python
print({
    "symbol": symbol,
    "from_date": FROM,
    "to_date": TO,
    "use_cache": True,
    "reports": len(reports),
    "tracing_nos": [r.tracing_no for r in reports],
})
```

این مستندات بر اساس نسخه‌ی اصلاح‌شده‌ی موجود در پوشه‌ی پروژه نوشته شده است. برای هر تغییر API، امضای واقعی تابع در همان نسخه با `inspect.signature` قابل بررسی است.
