@echo off
echo ============================================
echo  doc-ocr - Setup ^& Run
echo ============================================
echo.

REM Check Python
python --version 2>NUL
if ERRORLEVEL 1 (
    python3 --version 2>NUL
    if ERRORLEVEL 1 (
        echo ERROR: Python not found. Install Python 3.10+ first.
        pause
        exit /b 1
    )
)

REM Create virtual environment if needed
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate venv
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt

REM Run migrations
echo Running database migrations...
python manage.py migrate

REM Collect static files
echo Collecting static files...
python manage.py collectstatic --noinput 2>NUL

REM Import PDFs if folder exists
if exist "data\doc-ocr-documents" (
    echo Importing PDFs from data\doc-ocr-documents...
    python manage.py import_folder "data\doc-ocr-documents"
)

echo.
echo ============================================
echo  Setup complete! Starting server...
echo  Open http://127.0.0.1:9000 in your browser
echo ============================================
echo.

python manage.py runserver 0.0.0.0:9000
