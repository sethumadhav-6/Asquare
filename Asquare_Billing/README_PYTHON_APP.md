# A Square Interiors Python Billing App

This folder now includes a Python/Flask version of the billing system.

## Run on Windows

Double-click `run_python_app.bat`, or run these commands in PowerShell:

```powershell
cd D:\Wamp\htdocs\asquare_billing
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

Login:

```text
Username: Arun
Password: 8547@Arun
```

The Python app uses `asquare_billing.sqlite3` in this folder. Your old PHP files are unchanged.
