# نصب مستقل alilnvest_codal در Termux

برای اینکه کتابخانه بعد از نصب به مسیر حافظهٔ گوشی وابسته نباشد، نباید از `pip install -e .` استفاده شود. نصب editable فقط به پوشهٔ پروژه اشاره می‌کند.

## نصب صحیح

```bash
cd ~/storage/downloads/alilnvest_codal20
python -m pip install --upgrade --force-reinstall .
```

این دستور ابتدا از پروژه wheel می‌سازد و سپس فایل‌های `alilnvest_codal` را داخل `site-packages` محیط Python فعال کپی می‌کند.

یا از اسکریپت آماده استفاده کن:

```bash
cd ~/storage/downloads/alilnvest_codal20
bash install_termux.sh
```

## بررسی مستقل‌بودن نصب

```bash
python - <<'PY'
import pathlib
import alilnvest_codal

print("version:", alilnvest_codal.__version__)
print("path:", pathlib.Path(alilnvest_codal.__file__).resolve())
PY
```

مسیر باید شبیه یکی از این‌ها باشد:

```text
/data/data/com.termux/files/usr/lib/python3.x/site-packages/alilnvest_codal/...
```

یا مسیر `venv` فعال؛ نباید به این شکل باشد:

```text
/storage/emulated/0/Download/alilnvest_codal20/src/alilnvest_codal/...
```

بعد از مشاهدهٔ مسیر `site-packages`، می‌توانی پوشهٔ `alilnvest_codal20` را حذف یا جابه‌جا کنی و کتابخانه همچنان کار می‌کند.

## نکته دربارهٔ venv

اگر با venv کار می‌کنی، اول همان venv را فعال کن و سپس نصب را انجام بده:

```bash
source ~/venv/bin/activate
cd ~/storage/downloads/alilnvest_codal20
python -m pip install --upgrade --force-reinstall .
```

کتابخانه در همان venv نصب می‌شود و به venv دیگری وابسته نیست.
