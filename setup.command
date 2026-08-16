#!/bin/bash
# Doppelklickbares Setup für macOS. Richtet alles einmalig ein.
cd "$(dirname "$0")" || exit 1

echo ""
echo "════════════════════════════════════════════"
echo "  EV Slider – Einrichtung"
echo "════════════════════════════════════════════"
echo ""

# --- Python prüfen ---
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ Python fehlt auf diesem Mac."
  echo "   Bitte hier herunterladen und installieren: https://www.python.org/downloads/"
  echo "   Danach dieses Fenster schließen und setup.command erneut doppelklicken."
  echo ""
  read -r -p "Enter zum Schließen..."
  exit 1
fi
echo "✅ Python gefunden: $(python3 --version)"

# --- Virtuelle Umgebung + Pakete ---
echo ""
echo "→ Installiere Programmbibliotheken (dauert 1–3 Minuten)…"
python3 -m venv .venv >/dev/null 2>&1
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
if ! pip install --quiet -r requirements.txt; then
  echo "❌ Installation fehlgeschlagen. Bitte Ausgabe an Claude schicken."
  read -r -p "Enter zum Schließen..."
  exit 1
fi
echo "✅ Bibliotheken installiert"

echo ""
echo "→ Installiere den Browser für die Bildergalerie (ca. 150 MB)…"
python3 -m playwright install chromium >/dev/null 2>&1
echo "✅ Browser installiert"

# --- Konfiguration ---
[ -f config.yaml ] || cp config.example.yaml config.yaml

echo ""
echo "════════════════════════════════════════════"
echo "  Zugangsdaten"
echo "════════════════════════════════════════════"
echo "Beim Eintippen bzw. Einfügen bleibt das Feld aus Sicherheitsgründen leer –"
echo "das ist normal. Mit cmd+V einfügen, dann Enter."
echo "Was du nicht hast: einfach Enter drücken und später nachtragen."
echo ""

ask_secret() {          # $1 = Anzeigename, $2 = Variablenname
  printf "%s: " "$1"
  read -r -s value
  echo ""
  [ -n "$value" ] && echo "$2=$value" >> .env.tmp
}
ask_plain() {
  printf "%s: " "$1"
  read -r value
  [ -n "$value" ] && echo "$2=$value" >> .env.tmp
}

rm -f .env.tmp
ask_secret "GitHub-Token (github_pat_…)" "GITHUB_TOKEN"
ask_secret "Anthropic API-Key (sk-ant-…)" "ANTHROPIC_API_KEY"
ask_secret "Metricool Token" "METRICOOL_USER_TOKEN"
ask_plain  "Metricool User-ID" "METRICOOL_USER_ID"
ask_plain  "Metricool Blog-ID" "METRICOOL_BLOG_ID"

printf "Dein GitHub-Benutzername [gerkumproductions]: "
read -r ghuser
ghuser=${ghuser:-gerkumproductions}
printf "Name des Bilder-Repos [ev-slider-media]: "
read -r ghrepo
ghrepo=${ghrepo:-ev-slider-media}

python3 - "$ghuser" "$ghrepo" <<'PY'
import sys, re, pathlib
user, repo = sys.argv[1], sys.argv[2]
p = pathlib.Path("config.yaml"); s = p.read_text(encoding="utf-8")
s = re.sub(r'github_owner:.*', f'github_owner: "{user}"', s)
s = re.sub(r'github_repo:.*',  f'github_repo: "{repo}"', s)
p.write_text(s, encoding="utf-8")
print("✅ config.yaml angepasst")
PY

[ -f .env.tmp ] && mv .env.tmp .env && chmod 600 .env
echo "✅ Zugangsdaten gespeichert (Datei .env, nur für dich lesbar)"

echo ""
echo "════════════════════════════════════════════"
echo "  Fertig."
echo "  Ab jetzt nur noch: slider.command doppelklicken."
echo "════════════════════════════════════════════"
echo ""
read -r -p "Enter zum Schließen..."
