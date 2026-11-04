# نصب کامل alilnvest_codal در Termux

## روش پیشنهادی: نصب از فایل ZIP

فایل `alilnvest_codal13_1.0.0_termux.zip` را دانلود کنید و در پوشهٔ Download گوشی قرار دهید، سپس در Termux این دستورات را کامل کپی کنید:

```bash
pkg update -y
pkg upgrade -y
pkg install -y python unzip
termux-setup-storage
cd ~/storage/downloads
rm -rf alilnvest_codal13
unzip -q alilnvest_codal13_1.0.0_termux.zip -d alilnvest_codal13
cd alilnvest_codal13
python -m pip install --upgrade pip
python -m pip install -e .
```

اگر نام فایل دانلودشده فرق دارد، نام همان فایل را در دستور `unzip` جایگزین کنید.

## تست نصب

```bash
cd ~/storage/downloads/alilnvest_codal13
python -c "import alilnvest_codal; print(alilnvest_codal.__version__)"
```

باید `1.0.0` چاپ شود.

## استفادهٔ سریع

```bash
cd ~/storage/downloads/alilnvest_codal13
python - <<'PY'
import alilnvest_codal

reports = alilnvest_codal.income_statement(
    "فملی",
    from_date="1400/01/01",
    max_reports=1,
    progress=False,
)
print("تعداد گزارش‌ها:", len(reports))
if reports:
    print(reports[0].title)
    print(reports[0].row_code_df.shape)
PY
```

## تنظیم timeout و retry در Termux

پروفایل جدید سریع است و برای تست، بیشتر از چند ثانیه بین درخواست‌ها مکث نمی‌کند. برای اینترنت ضعیف می‌توانید timeout را افزایش دهید:

```bash
export CODAL_TIMEOUT=45
export CODAL_MAX_RETRIES=3
export CODAL_DELAY=1
export CODAL_LOG_LEVEL=INFO
```

برای دائمی‌کردن این تنظیمات:

```bash
cat >> ~/.bashrc <<'EOF'
export CODAL_TIMEOUT=45
export CODAL_MAX_RETRIES=3
export CODAL_DELAY=1
export CODAL_LOG_LEVEL=WARNING
EOF
source ~/.bashrc
```

## نصب بدون editable mode

اگر `pip install -e .` خطا داد، از این روش استفاده کنید:

```bash
cd ~/storage/downloads/alilnvest_codal13
python -m pip install httpx pandas lxml tenacity jdatetime
export PYTHONPATH="$PWD/src:$PYTHONPATH"
python -c "import alilnvest_codal; print(alilnvest_codal.__version__)"
```

## نکتهٔ مهم دربارهٔ timeout

اگر فقط یک گزارش با `max_reports=1` هم timeout می‌شود، مشکل معمولاً از دسترسی شبکهٔ Termux به `codal.ir` یا DNS است، نه از تعداد گزارش‌ها. این موارد را امتحان کنید:

```bash
ping -c 2 www.codal.ir
python -c "import httpx; print(httpx.get('https://www.codal.ir', timeout=30).status_code)"
```

در صورت خطای SSL یا DNS، ابتدا VPN/DNS شبکه را بررسی کنید و سپس دوباره تست را اجرا کنید.
