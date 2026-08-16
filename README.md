# EV Slider

Aus einem Engel & Völkers Exposé-Link wird ein fertiger Instagram-Karussell-Post:
Daten + Bilder ziehen → Slides im E&V-Look rendern → Posttext schreiben →
in Metricool auf einen Wochentag einplanen.

## Aufbau des Sliders

| Slide | Inhalt |
|-------|--------|
| 1 | Titelbild, Ortszeile, Exposé-Titel |
| 2 | Foto mit Bildtitel oben, darunter 2×2-Raster: Wohnfläche, Badezimmer, Baujahr, Zimmer |
| 3–10 | Objektbilder mit Bildtitel unten links |

### Posttext

Aufbau: Hook → CTA → Fließtext → CTA → Hashtags. Der Call-to-Action steht damit
an zweiter und an letzter Stelle. Das Stichwort wird pro Objekt erzeugt
(z. B. `LOGGIA`), die Formulierung steht in `caption.cta_template`.

Instagram erlaubt max. 10 Elemente pro Karussell – der Renderer kappt automatisch.

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium      # für die Bildergalerie, siehe unten
cp config.example.yaml config.yaml
```

Die E&V-Hausschriften liegen bereits in `assets/fonts/` (Head = Serif für
Headlines und Zahlenwerte, Text = Sans für Labels und Bildtitel).

Optional: das offizielle Icon-Set als `assets/icons/wohnflaeche.png`,
`badezimmer.png`, `baujahr.png`, `zimmer.png`. Fehlen die Dateien, zeichnet das
Tool schlichte Linien-Icons.

## Secrets (nie in die config.yaml)

```bash
export ANTHROPIC_API_KEY=...
export METRICOOL_USER_TOKEN=...
export METRICOOL_USER_ID=...
export METRICOOL_BLOG_ID=...      # via: python -m evslider.cli brands
```

## Betrieb per Telegram (empfohlen)

Link an einen Telegram-Bot schicken, alles andere passiert von allein. Läuft
als GitHub Action, kostet nichts und braucht weder Server noch eigenen Rechner.
Einrichtung: siehe TELEGRAM-EINRICHTUNG.txt.

Der Ablauf prüft alle fünf Minuten auf neue Nachrichten. Telegram merkt sich
selbst, was schon abgeholt wurde (Bestätigung über `offset`), deshalb braucht
es keine Datenbank.

## Bild-Hosting: GitHub (kostenlos)

Metricool lädt Bilder nur über öffentliche URLs, die direkt auf die Datei zeigen –
Canva-, Dropbox- oder Drive-Freigabelinks funktionieren dafür nicht, weil sie auf
eine Webseite verweisen. Kostenlos und dauerhaft geht es über ein öffentliches
GitHub-Repo:

1. Auf github.com ein leeres, öffentliches Repo anlegen, z. B. `ev-slider-media`.
2. Unter Settings → Developer settings → Personal access tokens einen Fine-grained
   Token für genau dieses Repo erzeugen, Recht "Contents: Read and write".
3. `export GITHUB_TOKEN=...` setzen und Name/Repo in die `config.yaml` eintragen.

Das Tool lädt die Slides dorthin und übergibt Metricool die
`raw.githubusercontent.com`-URLs. Direkt danach ruft es Metricools
`normalize`-Endpunkt auf, der die Dateien auf Metricools eigene Server kopiert –
der geplante Post hängt also nicht davon ab, dass das Repo erreichbar bleibt.

## Betrieb: lokal oder auf einem Server

Mit GitHub-Hosting reicht dein eigener Rechner: Tool starten, Links eingeben,
fertig. Ein Server lohnt nur, wenn es ohne deinen Rechner laufen soll oder
Kolleg:innen die Weboberfläche nutzen sollen.

### Variante Server (optional)

```bash
export EVSLIDER_PASSWORD=...              # Login für die Weboberfläche
export EVSLIDER_PUBLIC_URL=https://social.deine-domain.de
uvicorn webapp:app --host 0.0.0.0 --port 8000
```

Davor eine Domain mit HTTPS (Caddy oder nginx). Der Server liefert die
generierten Slides unter `/media/…` selbst aus – damit braucht Metricool keinen
zusätzlichen S3-Bucket. Alternativ `hosting.mode: s3`.

## Benutzung

```bash
# Sonntag: Links reinwerfen, alles läuft durch
python -m evslider.cli run \
  https://www.engelvoelkers.com/de/de/exposes/81b8d4b3-... \
  https://www.engelvoelkers.com/de/de/exposes/...

# nur Slides + Text erzeugen, nichts einplanen
python -m evslider.cli run <url> --no-schedule

# Metricool-Request nur anzeigen
python -m evslider.cli run <url> --dry-run

# prüfen, was der Scraper aus einer Seite herausliest
python -m evslider.cli probe <url>
```

Ergebnis pro Objekt in `out/<slug>/`: `*.jpg` (Slides), `caption.txt`, `expose.json`.

### Terminverteilung

Alle Posts werden automatisch auf Mo–Sa verteilt: erst ein Post pro Tag zur
Primärzeit, bei mehr als sechs Objekten kommt die zweite Uhrzeit dazu, danach
rollt es in die Folgewoche. Beispiel bei Abgabe am Sonntag:

| Objekte | Termine |
|---------|---------|
| 3 | Mo, Di, Mi je 09:30 |
| 6 | Mo–Sa je 09:30 |
| 8 | Mo–Sa 09:30 + Mo, Di 11:30 |

Vormittags, damit sich die Posts nicht mit den Reels am Abend überschneiden.

Konfiguration unter `schedule.days` / `schedule.times`.

## Wie die Bilder geholt werden

Die Exposé-Seite liefert serverseitig nur die ersten drei Galeriebilder aus – beim
Beispiel-Exposé sind es aber sechs. Der Zähler auf der Seite ("1/6") verrät die
echte Anzahl. Der Scraper arbeitet deshalb zweistufig:

1. Seite per HTTP laden, Objektdaten und die vorhandenen Bilder parsen.
2. Sind laut Zähler mehr Bilder da als gefunden, startet ein Headless-Chromium,
   klickt durch die Galerie und sammelt die nachgeladenen Bilder ein.

Stimmt die Zahl am Ende nicht, gibt das Tool eine Warnung aus statt still
einen halben Slider zu bauen. Steuerbar über `--browser auto|always|never`
bzw. `scrape.browser` in der Config.

## Offene Punkte vor dem Produktivbetrieb

1. **GitHub-Repo und Token** anlegen (siehe oben) – oder `hosting.mode: s3`
   bzw. `manual`, falls doch ein Server dazukommt.
2. **Metricool-Endpoint** einmal mit `--dry-run` gegen den echten Account
   verifizieren; Feldnamen der Scheduler-API können abweichen.
3. **Galerie-Selektoren** einmal live prüfen: `python -m evslider.cli probe <url>`.
   Die Klick-Logik ist gegen eine Nachbildung getestet, nicht gegen die echte
   Seite – die Buttons dort können anders heißen. Die Warnung "Nur X von Y
   Bildern gefunden" zeigt sofort, ob etwas klemmt.
4. **Freigabe**: Die Posts landen fertig im Metricool-Kalender und werden dort
   automatisch veröffentlicht (`metricool.auto_publish`). Im Kalender lassen sie
   sich bis zum Termin noch ändern oder löschen.
