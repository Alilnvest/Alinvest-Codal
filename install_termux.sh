#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# This is intentionally NOT `pip install -e .`: editable installs keep a
# reference to the source folder on phone storage. A normal local install
# builds a wheel and copies alilnvest_codal into the active Python environment.
pkg update -y
pkg install -y python
python -m pip install --upgrade pip
python -m pip install --upgrade --force-reinstall .

python - <<'PY'
import pathlib
import alilnvest_codal

path = pathlib.Path(alilnvest_codal.__file__).resolve()
print("alilnvest_codal", alilnvest_codal.__version__, "installed")
print("package path:", path)
print("self-contained site-packages install: ok")
PY
