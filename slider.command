#!/bin/bash
# Doppelklicken, Links einfügen, fertig.
cd "$(dirname "$0")" || exit 1

if [ ! -d .venv ]; then
  echo "❌ Noch nicht eingerichtet. Bitte zuerst setup.command doppelklicken."
  read -r -p "Enter zum Schließen..."
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo ""
echo "════════════════════════════════════════════"
echo "  EV Slider"
echo "════════════════════════════════════════════"
echo ""
echo "Exposé-Links einfügen – einen pro Zeile."
echo "Wenn du fertig bist: Enter drücken (leere Zeile)."
echo ""

urls=()
while true; do
  printf "Link %d: " "$(( ${#urls[@]} + 1 ))"
  read -r line
  [ -z "$line" ] && break
  urls+=("$line")
done

if [ ${#urls[@]} -eq 0 ]; then
  echo "Keine Links eingegeben."
  read -r -p "Enter zum Schließen..."
  exit 0
fi

echo ""
echo "Was soll passieren?"
echo "  1) Nur Slides und Text erzeugen (nichts wird eingeplant)"
echo "  2) Testlauf: alles erzeugen, Metricool-Aufruf nur anzeigen"
echo "  3) Scharf: in Metricool einplanen"
printf "Auswahl [1]: "
read -r choice
case "${choice:-1}" in
  2) mode="--dry-run" ;;
  3) mode="" ;;
  *) mode="--no-schedule" ;;
esac

echo ""
python -m evslider.cli run "${urls[@]}" $mode 2>&1 | tee letzter-lauf.txt

echo ""
echo "════════════════════════════════════════════"
echo "  Fertig. Die Slides liegen im Ordner 'out'."
echo "  Das Protokoll steht in 'letzter-lauf.txt' –"
echo "  bei Problemen diese Datei an Claude schicken."
echo "════════════════════════════════════════════"
echo ""
printf "Ordner mit den Slides öffnen? [j/n] "
read -r open_it
[ "$open_it" = "j" ] && open out 2>/dev/null

read -r -p "Enter zum Schließen..."
