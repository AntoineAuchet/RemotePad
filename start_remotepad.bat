@echo off
title RemotePad
cd /d "%~dp0"

REM ============================================================
REM RemotePad - lanceur unique - server.py srv-v14.1
REM Tout en un, un seul fichier pour tout le projet :
REM   1. Arrete proprement toute instance RemotePad deja lancee
REM   2. Trouve un Python : embarque dans python\ du projet,
REM      sinon installe sur la machine - sinon le CREE embarque
REM      automatiquement, une seule fois
REM   3. Installe les dependances manquantes
REM   4. Lance le serveur au premier plan
REM      Fermer cette fenetre = arreter RemotePad. Rien d'autre.
REM ============================================================

REM --- 1. Arret des instances precedentes - ports 8765 et 8766 ---
set "FOUND="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765 :8766" ^| findstr "LISTENING"') do (
    echo [RemotePad] Arret de l'instance precedente - processus %%p ...
    taskkill /PID %%p /F >nul 2>nul
    set "FOUND=1"
)
if defined FOUND (
    echo [RemotePad] Ancienne instance arretee - attente de liberation des ports ...
    timeout /t 1 >nul
)

REM --- 2. Recherche de Python, dans l'ordre :
REM       embarque du projet, py, python, python3 ---
set "PYTHON="
if exist "%~dp0python\python.exe" set "PYTHON=%~dp0python\python.exe"
if not defined PYTHON (
    py -c "import sys" >nul 2>nul && set "PYTHON=py"
)
if not defined PYTHON (
    python -c "import sys" >nul 2>nul && set "PYTHON=python"
)
if not defined PYTHON (
    python3 -c "import sys" >nul 2>nul && set "PYTHON=python3"
)

REM --- Pas de Python : creation automatique du Python embarque,
REM       une seule fois - le projet devient autonome ---
if defined PYTHON goto deps

echo [RemotePad] Aucun Python trouve sur cette machine.
echo [RemotePad] Creation du Python embarque dans python\ - automatique, une seule fois.
where curl >nul 2>nul || (
    echo [RemotePad] ERREUR : curl requis - inclus dans Windows 10 et 11.
    echo            Installez Python depuis python.org puis relancez.
    pause
    exit /b 1
)
if exist "%~dp0python\" (
    echo [RemotePad] ERREUR : le dossier python\ existe mais est incomplet.
    echo            Supprimez-le puis relancez.
    pause
    exit /b 1
)
set "PYVER=3.12.10"
echo [RemotePad] Telechargement de Python %PYVER% embarque - x64 ...
curl -L -o "%TEMP%\rp_python_embed.zip" "https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip" || (
    echo [RemotePad] ERREUR : telechargement impossible. Verifiez le reseau.
    pause
    exit /b 1
)
echo [RemotePad] Extraction dans python\ ...
mkdir python
tar -xf "%TEMP%\rp_python_embed.zip" -C python || (
    echo [RemotePad] ERREUR d'extraction - supprimez python\ et reessayez.
    pause
    exit /b 1
)
del "%TEMP%\rp_python_embed.zip" 2>nul
for %%f in ("%~dp0python\python*._pth") do echo import site>> "%%~f"
echo [RemotePad] Installation de pip ...
curl -L -o "%TEMP%\rp_get-pip.py" "https://bootstrap.pypa.io/get-pip.py" || (
    echo [RemotePad] ERREUR : telechargement de get-pip impossible.
    pause
    exit /b 1
)
python\python.exe "%TEMP%\rp_get-pip.py" --no-warn-script-location || (
    echo [RemotePad] ERREUR : installation de pip impossible.
    pause
    exit /b 1
)
del "%TEMP%\rp_get-pip.py" 2>nul
set "PYTHON=%~dp0python\python.exe"
echo [RemotePad] Python embarque pret - le projet est desormais autonome.
echo.

:deps
REM --- 3. Dependances, installees uniquement si absentes ---
echo [RemotePad] Python utilise : %PYTHON%
echo.
"%PYTHON%" -c "import websockets, qrcode, mss, PIL" 2>nul || (
    echo [RemotePad] Installation des dependances manquantes ...
    "%PYTHON%" -m pip install --disable-pip-version-check --no-warn-script-location --quiet websockets qrcode mss pillow
    if errorlevel 1 (
        echo [RemotePad] ERREUR pendant l'installation : reseau ? droits ?
        echo            Relancez le .bat, ou tapez manuellement :
        echo            %PYTHON% -m pip install websockets qrcode mss pillow
        pause
        exit /b 1
    )
)

REM --- 4. Lancement du serveur au premier plan ---
echo [RemotePad] Serveur en cours de lancement - ports 8765 et 8766.
echo [RemotePad] Laissez cette fenetre OUVERTE pendant l'utilisation.
echo [RemotePad] Arreter : fermez cette fenetre, ou Ctrl+C.
echo.
"%PYTHON%" -u server.py

echo.
echo [RemotePad] Le serveur s'est arrete. Une erreur est affichee ci-dessus ?
pause