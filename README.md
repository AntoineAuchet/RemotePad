<div align="center">

# 📱 RemotePad

**Votre téléphone devient trackpad + clavier sans fil pour votre PC Windows.**
Scannez un QR code — et contrôlez votre ordinateur depuis le canapé.
Aucune application. Aucun compte. Aucun cloud. 100 % réseau local.

![Plateforme](https://img.shields.io/badge/plateforme-Windows-0078D6)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Client](https://img.shields.io/badge/client-navigateur%20(HTML5)-E34F26?logo=html5&logoColor=white)
![Protocole](https://img.shields.io/badge/protocole-WebSocket-010101)
![Licence](https://img.shields.io/badge/licence-MIT-green)

*Conçu avec une approche UX : un langage d'interaction unique (« ancre + roue »), un tutoriel progressif, une accessibilité tactile et un retour haptique systématique.*

</div>

---

## ✨ Fonctionnalités

### 🖱️ Trackpad complet
| Geste | Action |
|-------|--------|
| Glisser 1 doigt | Déplacer le curseur (accélération adaptative) |
| Tap | Clic gauche · tap-tap = double-clic (mot) · tap-tap-tap = triple-clic (paragraphe) |
| Tap-tap + glisser | Sélection de texte |
| Appui long (750 ms) | Clic droit |
| Appui long + glisser | Drag & drop — **trace orange visible** pendant le drag |
| 2 doigts | Défilement vertical et horizontal (sens inversable) |

### ⏯️ Commandes média & clavier
- **Lecture/Pause, piste précédente/suivante**, et **reculer/avancer la bande son** (seek maintenu, via les commandes média Windows)
- **Volume** : appui court = muet, glisser = fader horizontal
- **Saisie texte** : accents et **emoji** (y compris hors plan BMP 🎯), retours à ligne sans valider, raccourcis d'édition (Ctrl+Z/Y/A)
- **Presse-papiers** : copier, couper, coller — l'ancre affiche l'action prédite

### 🗂️ Navigation
- Pages et onglets du navigateur (Alt+←/→, Ctrl+Tab), fermer/réouvrir l'onglet, Alt+Tab
- **Menu Démarrer**, réglages rapides (Win+A), aperçu de l'écran du PC sur le téléphone
- Éteindre / redémarrer / mettre en veille **avec confirmation obligatoire**

### 🎡 Le langage « ancre + roue »
Chaque bouton expose son action la plus probable en appui court ; l'appui long (380 ms) déploie une **roue radiale** de sa famille complète — glissez vers un picto, relâchez pour exécuter. La roue n'existe que pendant l'appui : zéro bouton fermer, zéro modale. Un tutoriel de 3 étapes accompagne la première connexion (rejouable à tout moment).

### 🔒 Sécurité (LAN de confiance, mais protégé quand même)
- Appairage par **code à 6 chiffres** régénéré à chaque lancement, transporté dans le QR (jamais en clair sur le réseau)
- **QR visible uniquement sur le PC** (les autres appareils du LAN ne voient pas le code)
- **Anti brute-force cumulatif par IP** + ban exponentiel (30 s → 1 h)
- Relâchement d'exclusivement les boutons réellement enfoncés, **suivi par connexion** (jamais d'événement souris orphelin)
- Rate-limit de l'aperçu écran, garde anti-frames binaires

## 🚀 Démarrage rapide

```bash
# 1. Cloner le dépôt
git clone https://github.com/<votre-user>/RemotePad.git
cd RemotePad

# 2. Lancer — c'est tout. Le lanceur :
#    - crée automatiquement un Python embarqué si besoin
#    - installe les dépendances (websockets, qrcode, mss, pillow)
#    - libère les ports si une instance tourne déjà
#    - ouvre la page QR dans le navigateur
./start_remotepad.bat
```

Puis : **scannez le QR code affiché sur le PC** avec l'appareil photo du téléphone → la page s'ouvre, l'appairage est automatique, le tutoriel se lance.

> **Prérequis** : Windows 10/11. Le lanceur utilise son propre Python embarqué — rien à installer au préalable.

## 🏗️ Architecture

```
┌─────────────┐   WebSocket :8765 (temps réel)   ┌────────────────────┐
│  Téléphone  │ ◄──────────────────────────────► │   PC — server.py   │
│ (navigateur)│   HTTP :8766 (client, QR)        │  Python + ctypes   │
└─────────────┘ ◄──────────────────────────────► │  (SendInput natif) │
                                                  └────────────────────┘
```

- **`server.py`** : serveur WebSocket (commandes temps réel) + HTTP (page QR, client) ; injection clavier/souris **native** via l'API Windows `SendInput` — pas de couche lourde type pyautogui, latence minimale
- **`client.html`** : application monopage, aucune installation sur le téléphone, thèmes clair/sombre, zéro requête externe (prête pour la PWA)
- **`start_remotepad.bat`** : lanceur tout-en-un (Python embarqué, dépendances, pré-vérification des ports)

## 📡 Protocole (extrait)

| Client → Serveur | Action |
|---|---|
| `join:<code>:<version>` | Appairage |
| `m<dx>;<dy>` | Déplacement relatif |
| `b<btn>;<état>` | Clics (par connexion) |
| `t<texte>` | Saisie Unicode (emoji, `\n`) |
| `p` `n` `r` `cleft` `cright` | Média + seek bande son |
| `c<id>` | Raccourcis (copy, closetab, alttab, winkey…) |
| `g` | Aperçu écran (JPEG, rate-limit 1/s) |
| `x<id>` | Extinction / redémarrage / veille (confirmés) |

## 🗺️ Feuille de route

- [x] Ancres & roues radiales complètes (4 angles, forme circulaire uniforme)
- [x] Sécurité : code 6 chiffres, anti brute-force par IP, QR local
- [x] Accessibilité : aria-labels, cibles tactiles ≥ 44 px, tutoriel de prise en main
- [x] Seek média natif Windows (WM_APPCOMMAND), emoji Unicode complets
- [x] Polices locales (zéro dépendance externe)
- [ ] Tests unitaires (parsing du protocole)
- [ ] PWA (installable sur l'écran d'accueil)
- [ ] Exécutable autonome (PyInstaller `--onefile`)

## 🧪 Tests

*(à venir — les tests du parsing protocole sont en cours d'écriture)*

```bash
pytest
```

## 🖼️ Captures d'écran

<!-- À ajouter : une capture du téléphone (roue ouverte) et une du PC (page QR).
     Placez les images dans un dossier docs/ puis :
![Roue radiale](docs/wheel.png)
![Page d'appairage](docs/qr-page.png)
-->

## 🙏 Crédits

- [remote-touchpad](https://github.com/Unrud/remote-touchpad) — source d'inspiration pour le protocole, l'accélération et le throttling
- Projet conçu et développé dans une démarche d'ergonomie cognitive : évaluations heuristiques (Nielsen, Bastien & Scapin), accessibilité WCAG 2.5.5, retours haptiques

## 👤 Auteur

**Antoine Auchet** — UX Researcher en ergonomie cognitive & IHM
[Portfolio](https://app.notion.com/p/3a23faecfde8819f9ec3e62c4449d797?pvs=204) · [LinkedIn](https://www.linkedin.com/in/antoine-auchet/) · [Malt](https://www.malt.fr/profile/antoineauchet)

## 📄 Licence

Distribué sous licence **MIT**. Voir [`LICENSE`](LICENSE).

---
<div align="center">
<sub>Fait avec ☕ et beaucoup de tests réels depuis un canapé.</sub>
</div>
