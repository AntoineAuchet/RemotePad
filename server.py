#!/usr/bin/env python3
"""RemotePad Server — contrôle souris/clavier depuis mobile (WebSocket).

Résumé des fonctionnalités :
- Appairage : code 6 chiffres généré aléatoirement à chaque lancement,
  transporté dans le hash de l'URL du QR (jamais envoyé au serveur HTTP).
- Sécurité : échecs d'appairage cumulés PAR IP (fenêtre 10 min), 3 échecs
  → ban exponentiel (30 s, doublé à chaque récidive, plafond 1 h) ; le QR
  n'est servi qu'en local — le code ne quitte jamais le PC (srv-v14).
- Souris : SendInput natif (déplacement relatif, clics, double-clic atomique,
  molette verticale et horizontale), relâchement ciblé : RemotePad ne
  relâche QUE les boutons qu'il a lui-même enfoncés (un UP orphelin,
  notamment RIGHTUP, fait apparaître le menu contextuel de la fenêtre
  sous le curseur — c'était la cause du bug du menu contextuel).
- Clavier : saisie Unicode via KEYEVENTF_UNICODE (accents, emoji), \\n = Shift+Entrée.
- Média : volume, muet, play/pause, piste précédente/suivante.
- Raccourcis : Copier/Coller/Couper/Ctrl+Z (et Ctrl+A/Ctrl+Y), onglets
  navigateur (Ctrl+Tab), Alt+Tab (bascule fenêtre), touche Windows —
  appuis simultanés de modificateurs en UNE salve SendInput atomique.
- Aperçu écran : capture (mss) → JPEG (Pillow) → base64, envoyé sur
  demande au client concerné (jamais en continu).
- Latence : ping/pong (uniquement après authentification).
- HTTP : page d'accueil avec QR, /client (no-store), /qr.
"""
import asyncio
import secrets
import socket
import string
import subprocess
import time
import webbrowser
import qrcode
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import os

CODE = "".join(secrets.choice(string.digits) for _ in range(6))
SERVER_VERSION = "srv-v14.4"
MAX_ATTEMPTS = 3        # échecs cumulés PAR IP (fenêtre FAIL_WINDOW) avant ban
BAN_SECONDS = 30        # durée du 1er ban — doublée à chaque récidive
FAIL_WINDOW = 600       # fenêtre de cumul des échecs : 10 min
banned = {}             # ip -> date d'expiration du ban
failures = {}           # ip -> horodatages des échecs d'appairage récents
strikes = {}            # ip -> nombre de bans déjà subis (ban exponentiel)


def record_failure(ip):
    """Échec d'appairage cumulé PAR IP, plus par connexion (srv-v14).

    L'ancien compteur était local au handler : envoyer 2 mauvais codes puis
    rouvrir la socket ne déclenchait JAMAIS le ban — un code à 4 chiffres
    tombait en minutes. Ici les échecs se cumulent sur une fenêtre glissante,
    même répartis sur plusieurs connexions.
    """
    now = time.time()
    recent = [t for t in failures.get(ip, []) if now - t < FAIL_WINDOW]
    recent.append(now)
    failures[ip] = recent
    return len(recent) >= MAX_ATTEMPTS


def ban_ip(ip):
    """Ban exponentiel : 30 s, puis 1 min, 2 min, 4 min… plafond 1 h."""
    strikes[ip] = strikes.get(ip, 0) + 1
    duration = min(BAN_SECONDS * 2 ** (strikes[ip] - 1), 3600)
    banned[ip] = time.time() + duration
    return duration


def purge_expired():
    """Purge à chaque connexion (audit P3) : bans expirés, échecs et récidives
    hors fenêtre glissante — les dictionnaires ne grossissent plus indéfiniment,
    et une IP qui se tient à carreau pendant FAIL_WINDOW repart de zéro."""
    now = time.time()
    for ip in [i for i, t in banned.items() if t <= now]:
        banned.pop(ip, None)
    for ip in list(failures):
        recent = [t for t in failures[ip] if now - t < FAIL_WINDOW]
        if recent:
            failures[ip] = recent
        elif ip not in banned:
            failures.pop(ip, None)
            strikes.pop(ip, None)


def cleanup_security_state():
    """Purge les bans expirés et les historiques d'échecs obsolètes.

    Appelée par la boucle de surveillance (1 s) : plus aucune entrée IP
    ne s'accumule indéfiniment en mémoire.
    """
    now = time.time()
    for ip in [ip for ip, until in banned.items() if now >= until]:
        del banned[ip]
    for ip in [ip for ip, lst in failures.items() if all(now - t >= FAIL_WINDOW for t in lst)]:
        del failures[ip]
authorized = set()   # clients authentifiés (pour diffuser le titre de fenêtre)

# Marqueur posé dans dwExtraInfo de TOUS nos SendInput : distinguait nos
# événements de ceux d'une autre source pendant le diagnostic du hook souris.
# Conservé (inoffensif, et utile si un nouvel outil interroge l'origine).
INJECT_MAGIC = 0x52504144

HOST = "0.0.0.0"
WS_PORT = 8765
HTTP_PORT = 8766
WORKSPACE = os.path.dirname(os.path.abspath(__file__))
QR_PATH = os.path.join(WORKSPACE, "remotepad_qr.png")
CLIENT_PATH = os.path.join(WORKSPACE, "client.html")


def check_ports_free():
    """Vérifie AVANT TOUT que les ports sont disponibles (srv-v14.1).

    Une instance précédente encore en vie (fenêtre oubliée, relance sans
    arrêt) donnait un serveur à moitié démarré : le HTTP de la nouvelle
    instance détournait silencieusement le port 8766 (SO_REUSEADDR sous
    Windows), la page du téléphone se chargeait donc depuis la NOUVELLE
    instance… dont le WebSocket mourait au bind (errno 10048), le port
    8765 restant aux mains de l'ANCIENNE. Résultat : QR affiché, page
    chargée, appairage impossible — et code d'appairage différent entre
    le QR et le serveur WS réellement contacté. On vérifie donc ici,
    avant de générer le QR ou d'ouvrir le navigateur.
    """
    for port in (WS_PORT, HTTP_PORT):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
            except OSError:
                print(f"[RemotePad] ERREUR : le port {port} est déjà occupé.")
                print("[RemotePad] Une autre instance de RemotePad tourne encore.")
                print("[RemotePad] Fermez sa fenêtre, ou lancez stop_remotepad.bat,")
                print("[RemotePad] ou en ligne de commande :")
                print("    netstat -ano | findstr :8765")
                print("    taskkill /PID <numéro_affiché> /F")
                raise SystemExit(1)


check_ports_free()


def get_lan_ip():
    """IP LAN robuste : aucun paquet n'est réellement envoyé (UDP)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


host_ip = get_lan_ip()
qr_url = f"http://{host_ip}:{HTTP_PORT}/client#{CODE}"
img = qrcode.make(qr_url)
img.save(QR_PATH)
print(f"[RemotePad] QR généré : {QR_PATH}")
print(f"[RemotePad] Version serveur : {SERVER_VERSION}")
print(f"[RemotePad] URL : {qr_url}")
print(f"[RemotePad] Code d'appairage : {CODE}")

import websockets

import ctypes
from ctypes import wintypes, Structure, Union, c_void_p, c_ulonglong, POINTER, sizeof

# --- SendInput ---
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001   # touches étendues (flèches…) — sans ce drapeau, Windows ignore l'événement (srv-v13)
user32 = ctypes.windll.user32


class MOUSEINPUT(Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", c_ulonglong)]


class KEYBDINPUT(Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", c_ulonglong)]


class _INPUTunion(Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTunion)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

user32.SendInput.argtypes = [wintypes.UINT, POINTER(INPUT), wintypes.INT]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, wintypes.INT]


def get_active_window_title():
    """Titre de la fenêtre au premier plan (affiché sur le téléphone)."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _send_input(n, inputs):
    arr = (INPUT * n)(*inputs)
    user32.SendInput(n, arr, sizeof(INPUT))


def mouse_move(dx, dy):
    mi = MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, INJECT_MAGIC)
    _send_input(1, [INPUT(INPUT_MOUSE, _INPUTunion(mi=mi))])


# Boutons actuellement enfoncés PAR RemotePad (0=gauche, 1=milieu, 2=droit).
# Règle d'or : ne JAMAIS envoyer un UP pour un bouton qu'on n'a pas enfoncé.
# Windows ouvre le menu contextuel dès qu'il voit un RIGHTUP orphelin :
# c'était la cause du bug du menu contextuel qui apparaissait tout seul.
buttons_down = {}   # bouton -> connexion propriétaire (srv-v14.4 : par connexion)


def mouse_click(button=0, down=True, owner=None):
    if button == 0:
        flag = 0x0002 if down else 0x0004  # gauche : LEFTDOWN / LEFTUP
        name = "GAUCHE"
    elif button == 2:
        flag = 0x0008 if down else 0x0010
        name = "DROIT "
    else:
        flag = 0x0020 if down else 0x0040
        name = "MILIEU"
    mi = MOUSEINPUT(0, 0, 0, flag, 0, INJECT_MAGIC)
    _send_input(1, [INPUT(INPUT_MOUSE, _INPUTunion(mi=mi))])
    if down:
        buttons_down[button] = owner
    else:
        buttons_down.pop(button, None)


def mouse_double_click(owner=None):
    # 10 ms entre les événements : séquence plus proche d'un vrai double-clic
    # humain (certains logiciels détectent mal les salves instantanées).
    mouse_click(0, True, owner)
    time.sleep(0.01)
    mouse_click(0, False, owner)
    time.sleep(0.01)
    mouse_click(0, True, owner)
    time.sleep(0.01)
    mouse_click(0, False, owner)


def mouse_wheel(delta):
    mi = MOUSEINPUT(0, 0, delta * 120, MOUSEEVENTF_WHEEL, 0, INJECT_MAGIC)
    _send_input(1, [INPUT(INPUT_MOUSE, _INPUTunion(mi=mi))])


def mouse_wheel_h(delta):
    mi = MOUSEINPUT(0, 0, delta * 120, MOUSEEVENTF_HWHEEL, 0, INJECT_MAGIC)
    _send_input(1, [INPUT(INPUT_MOUSE, _INPUTunion(mi=mi))])


def release_all_buttons(owner=None):
    """Relâche UNIQUEMENT les boutons encore enfoncés par RemotePad.

    Un UP orphelin (bouton jamais enfoncé) suffit à déclencher le menu
    contextuel de la fenêtre sous le curseur : l'ancienne version
    relâchait aveuglément gauche+droit+milieu à chaque clic et à
    l'appairage — d'où les menus contextuels spontanés.
    srv-v14.4 : les boutons sont détenus PAR CONNEXION — owner=None
    relâche tout (appairage), sinon seule la connexion qui les a enfoncés
    relâche les siens : deux téléphones ne se perturbent plus.
    """
    released = sorted(btn for btn, w in buttons_down.items()
                      if owner is None or w is owner)
    for btn in released:
        mouse_click(btn, down=False)
    return len(released)


def key_press(vk, scan=0, extended=False):
    flags = KEYEVENTF_EXTENDEDKEY if extended else 0
    ki_down = KEYBDINPUT(vk, scan, flags, 0, INJECT_MAGIC)
    ki_up = KEYBDINPUT(vk, scan, flags | KEYEVENTF_KEYUP, 0, INJECT_MAGIC)
    _send_input(2, [INPUT(INPUT_KEYBOARD, _INPUTunion(ki=ki_down)), INPUT(INPUT_KEYBOARD, _INPUTunion(ki=ki_up))])


def key_combo(modifiers, vk, vk_extended=False):
    """Combo type Ctrl+C : modificateurs enfoncés, touche, puis tout relâché.

    Toute la séquence part dans UNE SEULE salve SendInput (atomique) :
    modificateurs DOWN, touche DOWN/UP, modificateurs UP en ordre inverse.
    Un modificateur ne peut donc jamais rester enfoncé, même en cas d'erreur.
    vk_extended : la touche est une touche ÉTENDUE (flèches…) — sans
    KEYEVENTF_EXTENDEDKEY, Windows IGNORE l'événement (bug ±15 s, srv-v13).
    """
    vkf = KEYEVENTF_EXTENDEDKEY if vk_extended else 0
    downs = [_ki(m) for m in modifiers]
    downs.append(_ki(vk, vkf))
    ups = [_ki(vk, vkf | KEYEVENTF_KEYUP)]
    ups.extend(_ki(m, KEYEVENTF_KEYUP) for m in reversed(modifiers))
    _send_input(len(downs) + len(ups), downs + ups)


# --- Saisie texte Unicode (KEYEVENTF_UNICODE) ---
KEYEVENTF_UNICODE = 0x0004
VK_SHIFT = 0x10
VK_RETURN = 0x0D


def _ki(vk, flags=0):
    return INPUT(INPUT_KEYBOARD, _INPUTunion(ki=KEYBDINPUT(vk, 0, flags, 0, INJECT_MAGIC)))


def normalize_newlines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _ki_unicode(unit, up=False):
    """Un événement Unicode : wVk=0, wScan=unité UTF-16, drapeau UNICODE."""
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(INPUT_KEYBOARD, _INPUTunion(ki=KEYBDINPUT(0, unit, flags, 0, INJECT_MAGIC)))


def type_text(text):
    """Saisie directe via KEYEVENTF_UNICODE — gère \n (Shift+Entrée), accents, emoji.

    srv-v14.2 : les caractères hors plan BMP (la plupart des emoji, ex. 🎯
    U+1F3AF) sont envoyés en PAIRES DE SUBSTITUTS UTF-16 — wScan est un WORD
    de 16 bits, l'ancien ord(ch) tronquait tout caractère > U+FFFF et
    produisait un caractère erroné.
    """
    text = normalize_newlines(text)
    inputs = []
    for ch in text:
        if ch == '\n':
            # Shift+Entrée = saut de ligne sans valider le message
            inputs.append(_ki(VK_SHIFT))
            inputs.append(_ki(VK_RETURN))
            inputs.append(_ki(VK_RETURN, KEYEVENTF_KEYUP))
            inputs.append(_ki(VK_SHIFT, KEYEVENTF_KEYUP))
        else:
            units = ch.encode("utf-16-le")
            for i in range(0, len(units), 2):
                unit = units[i] | (units[i + 1] << 8)
                inputs.append(_ki_unicode(unit))
                inputs.append(_ki_unicode(unit, up=True))
    if inputs:
        _send_input(len(inputs), inputs)


# Touches média
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3

# --- Raccourcis clavier (Ctrl+…) ---
VK_CONTROL = 0x11
VK_MENU = 0x12        # Alt
VK_KEY_Z = 0x5A
VK_KEY_Y = 0x59
VK_KEY_A = 0x41
VK_KEY_C = 0x43
VK_KEY_V = 0x56
VK_KEY_X = 0x58
VK_KEY_W = 0x57
VK_KEY_T = 0x54
VK_TAB = 0x09
VK_LWIN = 0x5B
VK_LEFT = 0x25         # flèche gauche — touche ÉTENDUE : drapeau KEYEVENTF_EXTENDEDKEY obligatoire
VK_RIGHT = 0x27        # flèche droite — idem


# --- Commandes multimédia « shell » (WM_APPCOMMAND) — srv-v14.3 ---
# Les touches média (VK_MEDIA_*) sont GLOBALES, mais il n'existe pas
# d'équivalent pour reculer/avancer la BANDE SON : la flèche simple ne fait
# un seek QUE si la fenêtre du lecteur a le focus — et tous les lecteurs ne
# la gèrent pas. WM_APPCOMMAND (le message que génère une touche média) est
# le canal officiel : APPCOMMAND_MEDIA_REWIND / FASTFORWARD est le
# « reculer/avancer » attendu par les lecteurs qui le gèrent (Lecteur
# Windows, WMP…). La fenêtre renvoie True si elle a TRAITÉ la commande,
# False sinon → repli sur la flèche étendue (YouTube, VLC…).
WM_APPCOMMAND = 0x0319
APPCOMMAND_MEDIA_FASTFORWARD = 49
APPCOMMAND_MEDIA_REWIND = 50
FAPPCOMMAND_KEY = 2   # périphérique clavier : bits 28-31 du lParam

user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = wintypes.BOOL   # TRUE si la fenêtre a TRAITÉ la commande (LRESULT n'existe pas dans wintypes)

def app_command(cmd):
    """Commande média shell à la fenêtre au premier plan ; True si traitée."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    lparam = (FAPPCOMMAND_KEY << 28) | (cmd << 16)
    return bool(user32.SendMessageW(hwnd, WM_APPCOMMAND, hwnd, lparam))


def do_shortcut(sid):
    """Exécute un raccourci par identifiant : copy, paste, cut, undo, redo, all,
    tabprev/tabnext (onglets navigateur), closetab (fermer l'onglet, Ctrl+W),
    reopen (réouvrir le dernier onglet fermé, Ctrl+Maj+T), alttab (bascule
    fenêtre), winkey (Démarrer), back/fwd (page précédente/suivante,
    Alt+flèches), settings (réglages rapides, Win+A), left/right (reculer/
    avancer la bande son : commande shell WM_APPCOMMAND en priorité,
    flèche étendue en repli — srv-v14.3)."""
    table = {
        "copy":     lambda: key_combo([VK_CONTROL], VK_KEY_C),
        "paste":    lambda: key_combo([VK_CONTROL], VK_KEY_V),
        "cut":      lambda: key_combo([VK_CONTROL], VK_KEY_X),
        "undo":     lambda: key_combo([VK_CONTROL], VK_KEY_Z),
        "redo":     lambda: key_combo([VK_CONTROL], VK_KEY_Y),
        "all":      lambda: key_combo([VK_CONTROL], VK_KEY_A),
        "tabprev":  lambda: key_combo([VK_CONTROL, VK_SHIFT], VK_TAB),
        "tabnext":  lambda: key_combo([VK_CONTROL], VK_TAB),
        "closetab": lambda: key_combo([VK_CONTROL], VK_KEY_W),
        "reopen":   lambda: key_combo([VK_CONTROL, VK_SHIFT], VK_KEY_T),
        "alttab":   lambda: key_combo([VK_MENU], VK_TAB),
        "winkey":   lambda: key_press(VK_LWIN),
        "back":     lambda: key_combo([VK_MENU], VK_LEFT, vk_extended=True),
        "fwd":      lambda: key_combo([VK_MENU], VK_RIGHT, vk_extended=True),
        "settings": lambda: key_combo([VK_LWIN], VK_KEY_A),
        "left":     lambda: (app_command(APPCOMMAND_MEDIA_REWIND) or key_press(VK_LEFT, extended=True)),
        "right":    lambda: (app_command(APPCOMMAND_MEDIA_FASTFORWARD) or key_press(VK_RIGHT, extended=True)),
    }
    fn = table.get(sid)
    if fn:
        fn()


# --- Aperçu écran (mss + Pillow — pip install mss pillow) ---
try:
    import base64
    import io
    import mss
    from PIL import Image
    SCREEN_PREVIEW_OK = True
except ImportError:
    SCREEN_PREVIEW_OK = False
    print("[RemotePad] Aperçu écran indisponible : pip install mss pillow")


CAPTURE_MIN_INTERVAL = 1.0    # secondes entre deux aperçus 'g' (srv-v14.4)
capture_state = {"last": 0.0}  # dict mutable : pas de mot-clé global dans le handler

def grab_screen():
    """Capture l'écran principal → JPEG base64 (largeur max 480 px, ~50 Ko).

    Robustesse : écran principal (monitors[1]), repli sur monitors[0] si
    absent — et JAMAIS d'échec silencieux (le handler 'g' remonte l'erreur).
    """
    # Rate-limit (srv-v14.4, audit P3) : mss + Pillow + JPEG base64 coûte du
    # CPU à chaque requête 'g' — minimum 1 s entre deux captures, les
    # requêtes trop rapprochées sont ignorées silencieusement (le client
    # retapera l'aperçu). Le pointeur est posé AVANT la capture : le temps
    # de traitement de la capture ne compte pas dans l'intervalle.
    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(monitor)
        img = Image.frombytes('RGB', shot.size, shot.rgb, 'raw', 'RGB', shot.size[0] * 3)
        img.thumbnail((480, 480))
        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=70)
        return base64.b64encode(buf.getvalue()).decode('ascii')


def client_ip(websocket):
    try:
        return websocket.remote_address[0]
    except Exception:
        return "?"


async def handler(websocket):
    ip = client_ip(websocket)
    purge_expired()   # srv-v14.4 : ban/échecs/récidives périmés ne s'accumulent plus
    until = banned.get(ip, 0)
    if time.time() < until:
        # IP bannie : message explicite (le téléphone affiche le motif au
        # lieu de boucler en reconnexion) puis fermeture.
        try:
            await websocket.send("banned")
        except Exception:
            pass
        await websocket.close(code=1008, reason="banni")
        return

    print(f"[RemotePad] Client {ip} connecté — en attente du code...")
    auth_ok = False
    try:
        await websocket.send("need-code")
        async for message in websocket:
            if not isinstance(message, str):
                # Trame binaire ignorée proprement : un message bytes ferait
                # échouer message.strip() et cracher le handler (srv-v14.2).
                continue
            if not auth_ok:
                # L'auth passe AVANT tout — y compris le ping.
                # Formats acceptés : "join:CODE" ou "join:CODE:vX" (version client).
                parts = message.strip().split(":")
                if len(parts) >= 2 and parts[0] == "join" and parts[1] == CODE:
                    auth_ok = True
                    failures.pop(ip, None)   # appairage réussi : échecs oubliés
                    ver = parts[2] if len(parts) > 2 else "?"
                    print(f"[RemotePad] Code validé — {ip} autorisé (client {ver})")
                    await websocket.send("ok")
                    authorized.add(websocket)
                    n = release_all_buttons()      # sécurité : libère tout bouton encore enfoncé (rien sinon)
                    print(f"[RemotePad] {n} bouton(s) encore enfoncé(s) relâché(s)" if n
                          else "[RemotePad] Aucun bouton enfoncé — rien à relâcher")
                    await websocket.send("w" + get_active_window_title())
                else:
                    # Échec : cumul PAR IP (fenêtre glissante) — répartir
                    # les essais sur plusieurs connexions ne contourne
                    # plus le ban (srv-v14, audit P1).
                    if record_failure(ip):
                        duration = ban_ip(ip)
                        print(f"[RemotePad] {ip} banni {duration:.0f}s "
                              f"({MAX_ATTEMPTS} échecs cumulés, ban n°{strikes[ip]})")
                        await websocket.send("banned")
                        await websocket.close(code=1008, reason="banned")
                        return
                    await websocket.send("error")
                continue
            if message == "ping":
                await websocket.send("pong")
                continue
            # Journal : tout sauf les mouvements (trop verbeux).
            # Les clics "b" SONT journalisés temporairement pour diagnostiquer
            # le bug du menu contextuel — à retirer une fois résolu.
            if not message.startswith("m"):
                print(f"[RemotePad] <- {message[:48]!r}")
            cmd = message[:1]
            if cmd == "m":
                parts = message[1:].split(";")
                if len(parts) == 2:
                    try:
                        mouse_move(int(parts[0]), int(parts[1]))
                    except ValueError:
                        pass
            elif cmd == "b":
                parts = message[1:].split(";")
                if len(parts) == 2:
                    try:
                        btn, state = int(parts[0]), int(parts[1])
                        if btn == 0 and state == 2:
                            mouse_double_click(owner=websocket)
                        elif state == 0:
                            # Relâche les boutons de CETTE connexion — jamais d'UP
                            # orphelin, et jamais le bouton d'une autre
                            # connexion (srv-v14.4 : état par connexion).
                            release_all_buttons(websocket)
                        else:
                            mouse_click(btn, down=(state == 1), owner=websocket)
                    except ValueError:
                        pass
            elif cmd == "s":
                try:
                    dy = int(message[1:])
                    if dy != 0:
                        mouse_wheel(-dy)  # doigts vers le bas = contenu vers le bas
                except ValueError:
                    pass
            elif cmd == "h":
                try:
                    dx = int(message[1:])
                    if dx != 0:
                        mouse_wheel_h(dx)
                except ValueError:
                    pass
            elif cmd == "v":
                direction = message[1:]
                if direction == "1":
                    key_press(VK_VOLUME_UP)
                elif direction == "-1":
                    key_press(VK_VOLUME_DOWN)
                elif direction == "0":
                    key_press(VK_VOLUME_MUTE)
            elif cmd == "p":
                key_press(VK_MEDIA_PLAY_PAUSE)
            elif cmd == "n":
                key_press(VK_MEDIA_NEXT_TRACK)
            elif cmd == "r":
                key_press(VK_MEDIA_PREV_TRACK)
            elif cmd == "t":
                type_text(message[1:])
            elif cmd == "k":
                try:
                    key_press(int(message[1:]))
                except ValueError:
                    pass
            elif cmd == "c":
                # Raccourcis : ccopy / cpaste / ccut / cundo / credo / call
                do_shortcut(message[1:])
            elif cmd == "g":
                # Rate-limit (srv-v14.4, audit P3) : une capture est coûteuse
                # (mss + Pillow + JPEG base64) — 1 s minimum entre deux
                # aperçus, les requêtes trop rapprochées sont ignorées.
                now = time.time()
                if now - capture_state["last"] < CAPTURE_MIN_INTERVAL:
                    continue
                capture_state["last"] = now
                # Aperçu écran : capture → JPEG base64 → 'i<data>' (sur demande).
                # Échec → 'i!' : le client affiche l'erreur (jamais d'échec silencieux).
                if not SCREEN_PREVIEW_OK:
                    await websocket.send("i!")
                else:
                    try:
                        data = await asyncio.get_running_loop().run_in_executor(None, grab_screen)
                        await websocket.send("i" + data)
                    except Exception as exc:
                        print(f"[RemotePad] Aperçu : échec de la capture — {exc!r}")
                        await websocket.send("i!")
            elif cmd == "x":
                # Extinction / redémarrage / veille — confirmées côté client avant envoi.
                # shutdown : délai 5 s (annulable sur le PC via `shutdown /a`).
                # Identifiants alignés sur le client v14 : off / reboot / sleep.
                # (les anciens shutdown / restart restent acceptés par robustesse)
                action = message[1:]
                print(f"[RemotePad] Demande système : {action}")
                if action in ("off", "shutdown"):
                    subprocess.run(["shutdown", "/s", "/t", "5"], capture_output=True)
                elif action in ("reboot", "restart"):
                    subprocess.run(["shutdown", "/r", "/t", "5"], capture_output=True)
                elif action == "sleep":
                    subprocess.run(["rundll32", "powrprof.dll,SetSuspendState", "0,1,0"], capture_output=True)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        authorized.discard(websocket)
        release_all_buttons(websocket)   # srv-v14.4 : SES boutons relâchés — les autres connexions ne sont pas perturbées
        print(f"[RemotePad] Client {ip} déconnecté")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        # Le QR CONTIENT le code d'appairage : page d'accueil et image ne
        # sont servies QUE depuis le PC (localhost). Un appareil du LAN qui
        # ouvre http://IP:8766/ ne voit plus le code — seul /client reste
        # accessible depuis le réseau (srv-v14, audit P1 : le QR était
        # lisible par tout le LAN, ce qui court-circuitait l'appairage).
        is_local = self.client_address[0] in ("127.0.0.1", "::1")
        if not is_local and path in ("/", "/qr", "/remotepad_qr.png"):
            body = "RemotePad : scannez le QR affiché sur le PC.".encode("utf-8")
            self.send_response(404)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))   # réponse cadrée (audit P3)
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/qr" or path == "/remotepad_qr.png":
            with open(QR_PATH, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))   # réponse cadrée (audit P3)
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/client"):
            with open(CLIENT_PATH, "r", encoding="utf-8") as f:
                body = f.read().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))   # réponse cadrée (audit P3)
            self.end_headers()
            self.wfile.write(body)
            return
        if not is_local:
            # Tout autre chemin depuis le LAN (favicon, etc.) : 404 net,
            # pas de page d'accueil qui divulgue le QR.
            body = "404 — scannez le QR affiché sur le PC.".encode("utf-8")
            self.send_response(404)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))   # réponse cadrée (audit P3)
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>RemotePad — Appairage</title>
<style>
:root {{ --bg:#0d0d10; --fg:#f2f2f7; --accent:#7ee787; --muted:#8e8e93; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--fg); min-height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; padding:40px 20px; }}
.container {{ max-width:420px; width:100%; text-align:center; }}
h1 {{ font-weight:600; letter-spacing:-0.03em; font-size:2.2rem; margin-bottom:6px; }}
.subtitle {{ color:var(--muted); font-size:0.95rem; margin-bottom:28px; }}
.qr-img {{ border-radius:16px; overflow:hidden; box-shadow:0 20px 40px rgba(0,0,0,0.35); display:inline-block; line-height:0; }}
.qr-img img {{ width:240px; height:240px; display:block; }}
.note {{ margin-top:16px; color:var(--muted); font-size:0.85rem; }}
</style></head><body>
<div class="container">
  <h1>RemotePad</h1>
  <p class="subtitle">Contrôlez votre ordinateur depuis votre téléphone</p>
  <div class="qr-img"><img src="/qr" alt="QR Code"></div>
  <p class="note">Scannez le QR avec votre téléphone pour ouvrir RemotePad.</p>
</div>
</body></html>"""
        body = html.encode("utf-8")
        self.send_header("Content-Length", str(len(body)))   # réponse cadrée (audit P3)
        self.end_headers()
        self.wfile.write(body)


httpd = HTTPServer((HOST, HTTP_PORT), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
print(f"[RemotePad] HTTP démarré sur http://{host_ip}:{HTTP_PORT}/  (QR, /client)")

try:
    webbrowser.open(f"http://localhost:{HTTP_PORT}/")
except Exception:
    pass


async def watch_active_window():
    """Diffuse le titre de la fenêtre active aux clients quand il change."""
    last = None
    while True:
        await asyncio.sleep(1.0)
        cleanup_security_state()          # bans/failures expirés → mémoire propre
        title = get_active_window_title()
        if title != last:
            last = title
            for client in list(authorized):
                try:
                    await client.send("w" + title)
                except Exception:
                    authorized.discard(client)


async def main():
    print(f"[RemotePad] Serveur prêt. Code d'appairage : {CODE}")
    asyncio.create_task(watch_active_window())
    async with websockets.serve(handler, HOST, WS_PORT) as server:
        print(f"[RemotePad] WebSocket démarré sur ws://{host_ip}:{WS_PORT}/")
        await server.serve_forever()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\n[RemotePad] Arrêt.")