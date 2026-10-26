@echo off
REM RemotePad — lancement en double-clic
REM Utilise le python du projet (le même environnement que celui du kernel)
set PYTHON="C:\Users\Sentient\AppData\Local\hermes\tools\PYTHON~1.7_2\python.exe"
echo [RemotePad] Lancement du serveur...
start /b "" %PYTHON% -u server.py
echo [RemotePad] Serveur lancé en arriere-plan (port 8765 / 8766).
echo [RemotePad] Ouvrez http://localhost:8766/ sur le PC et scannez le QR.
echo [RemotePad] Appuyez sur une touche pour fermer ce message (le serveur continue).
pause
