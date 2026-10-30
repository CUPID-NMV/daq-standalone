#!/usr/bin/env bash
#
# Scan in soglia del self-trigger del V1742, UN CANALE ALLA VOLTA.
#
# scan_v1742.sh muove tutti i canali insieme, con lo stesso offset: con SiPM e
# PMT sulla stessa scheda non misura niente di utile, perche' i SiPM saturano
# la DAQ (~850 Hz) a offset dove i PMT sono appena sopra un fotoelettrone.
# Qui ogni canale ha la sua curva: gli altri stanno alla soglia massima e non
# scattano, e finito il canale tornano tutti agli offset di partenza
# (noise_scan.py --canale). Come l'altro, cambia le soglie a run in corso.
#
#   tools/scan_v1742_canali.sh -s 60 "20 21: 400 200 100 50" "16 17 18: 1200 600 300 100"
#
# Ogni gruppo e' "canali: offset". Alla fine un grafico per gruppo in plots/,
# che la pagina di controllo elenca fra gli scan insieme ai JSON di ogni canale.
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/tools/scan_v1742_canali.sh"
CONTROLLORE="${CONTROLLORE:-http://localhost:8766}"
CHI="${CHI:-scan_v1742_canali}"
# Dove vanno JSON e grafici: plots/, che la pagina elenca. Si cambia solo per
# provare lo script senza mescolare le prove alle misure.
PLOTS="${PLOTS:-$ROOT/plots}"
SECONDI=60
FERMA=0
RIMETTI=()

uso() {
    sed -n '3,15p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    echo
    echo "Opzioni:"
    echo "  -s <secondi>              durata di ogni punto (default: $SECONDI)"
    echo "  -F                        a fine scan ferma la run, dal controllore"
    echo "  -R sezione.chiave=valore  a fine scan rimette questa chiave del TOML, dal"
    echo "                            controllore (anche piu' volte); poi riavvia il"
    echo "                            monitor, perche' segua OutputDir"
    exit "${1:-0}"
}

while getopts "s:FR:h" opt; do
    case "$opt" in
        s) SECONDI="$OPTARG" ;;
        F) FERMA=1 ;;
        R) RIMETTI+=("$OPTARG") ;;
        h) uso 0 ;;
        *) uso 1 ;;
    esac
done
shift $((OPTIND - 1))
(( $# > 0 )) || uso 1

# --- Controlli prima di partire: gli stessi di scan_v1742.sh ---------------
if ! pgrep -f "main/DAQ-WC" > /dev/null; then
    echo "ERRORE: non c'e' nessuna DAQ in esecuzione: la run va avviata prima." >&2
    exit 1
fi
TOML="${TOML:-$ROOT/config/run-local.toml}"
DATI=$(python3 - "$TOML" <<'EOF'
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    print(tomllib.load(f).get("digitizer", {}).get("OutputDir") or "")
EOF
)
[[ -z "$DATI" ]] && DATI="$ROOT/data"
STATO="$DATI/live-status.json"
if [[ ! -f "$STATO" ]] || (( $(date +%s) - $(stat -c %Y "$STATO") > 600 )); then
    echo "ERRORE: $STATO manca o e' vecchio: la run in corso non usa il" >&2
    echo "self-trigger, o scrive in un'altra cartella." >&2
    exit 1
fi

# --- La pagina di controllo deve sapere che c'e' uno scan -------------------
# E' lo stesso file che scrive lei quando lo scan lo lancia dalla pagina:
# cosi' mostra l'avanzamento, offre "Stop scan" (SIGTERM al gruppo, che
# noise_scan.py intercetta per rimettere gli offset), e non lascia avviare o
# fermare run mentre lo scan le sta usando.
REGISTRO="${REGISTRO:-$ROOT/data/scan-in-corso.json}"
python3 - "$REGISTRO" "$$" "$(ps -o pgid= $$ | tr -d ' ')" "$SCRIPT" "$SECONDI" "$*" <<'EOF'
import json, sys, time
f, pid, pgid, script, sec, valori = sys.argv[1:]
json.dump({"pid": int(pid), "pgid": int(pgid), "tipo": "v1742", "script": script,
           "valori": "per channel  " + valori, "secondi": float(sec),
           "avviato": time.time()}, open(f, "w"))
EOF
deregistra() {
    python3 - "$REGISTRO" "$$" <<'EOF'
import json, os, sys
try:
    if json.load(open(sys.argv[1])).get("pid") == int(sys.argv[2]):
        os.remove(sys.argv[1])
except (OSError, ValueError):
    pass
EOF
}
# Fermato da fuori: gli offset li ha gia' rimessi noise_scan.py, la run resta
# com'e'. Chi ha premuto Stop sta guardando la pagina e decide lui.
fermato() {
    echo "scan fermato da fuori: offset di partenza rimessi, run lasciata accesa."
    deregistra
    exit 143
}
trap fermato TERM INT
trap deregistra EXIT

STAMP=$(date +%Y%m%d_%H%M%S)
INIZIO=$(date +%s)
echo "=== Scan del self-trigger V1742, un canale alla volta ==="
echo "dati    : $DATI"
echo "durata  : $SECONDI s per punto"
for g in "$@"; do echo "gruppo  : $g"; done
echo

GRAFICI=()
completo=1
for g in "$@"; do
    canali="${g%%:*}"
    offsets="${g#*:}"
    json=()
    for ch in $canali; do
        echo "=== ch$ch ==="
        if ! python3 "$ROOT/tools/noise_scan.py" --canale "$ch" --offsets $offsets \
                --seconds "$SECONDI" -d "$DATI" -o "$PLOTS"; then
            echo "ERRORE: lo scan di ch$ch non e' arrivato in fondo: mi fermo qui." >&2
            completo=0
        fi
        # Il JSON del canale, se questo giro l'ha scritto (anche a scan
        # interrotto contiene i punti misurati).
        f=$(ls -t "$PLOTS"/noise_scan_*_ch"$ch".json 2>/dev/null | head -1)
        if [[ -n "$f" ]] && (( $(stat -c %Y "$f") >= INIZIO )); then
            json+=("$f")
        fi
        (( completo )) || break
        echo
    done
    if (( ${#json[@]} > 0 )); then
        nome="$PLOTS/scan_canali_${STAMP}_ch$(echo $canali | tr ' ' '-').png"
        # La conversione in mV la sceglie calibrazione.py dalla larghezza
        # degli impulsi, come fa la pagina di controllo. plot_scan.py da solo
        # guarda la sola frequenza, e a 750 MS/s scriveva "NOT CALIBRATED"
        # anche sui SiPM, che una calibrazione misurata ce l'hanno.
        mv=$(python3 - "$ROOT" "${json[@]}" <<'EOF'
import json, os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "tools"))
import calibrazione
valori = set()
for f in sys.argv[2:]:
    d = json.load(open(f))
    tag = next((t for t in ("5Gs", "2.5Gs", "1Gs", "750Ms") if t in str(d.get("file", ""))), None)
    valori.add(calibrazione.scegli(tag, d.get("larghezza_fwhm_ns"))[0])
print(valori.pop() if len(valori) == 1 and None not in valori else "")
EOF
)
        opz=(--logy)
        [[ -n "$mv" ]] && opz+=(--mv-per-offset "$mv")
        python3 "$ROOT/tools/plot_scan.py" "${opz[@]}" -o "$nome" "${json[@]}" && GRAFICI+=("$nome")
    fi
    (( completo )) || break
done

echo
echo "=== Grafici ==="
for f in "${GRAFICI[@]}"; do echo "  $f"; done

# --- Dopo lo scan -----------------------------------------------------------
# Il registro va tolto PRIMA di chiedere al controllore di fermare la run:
# finche' c'e', il controllore rifiuta ("A scan is running").
deregistra
if (( FERMA )); then
    echo; echo "=== Fermo la run ==="
    curl -s -m 120 -X POST "$CONTROLLORE/api/ferma?chi=$CHI"; echo
fi
if (( ${#RIMETTI[@]} > 0 )); then
    echo; echo "=== Rimetto il TOML ==="
    python3 - "$CONTROLLORE" "$CHI" "${RIMETTI[@]}" <<'EOF'
import json, sys, urllib.request
url, chi, voci = sys.argv[1], sys.argv[2], sys.argv[3:]
mod = []
for v in voci:
    chiave, _, valore = v.partition("=")
    sezione, _, nome = chiave.partition(".")
    mod.append([sezione, nome, valore])
req = urllib.request.Request(url + "/api/config", method="POST",
                             data=json.dumps({"chi": chi, "modifiche": mod}).encode(),
                             headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req, timeout=30).read().decode())
EOF
    echo; echo "=== Riavvio il monitor ==="
    curl -s -m 60 -X POST "$CONTROLLORE/api/monitor?azione=ferma&chi=$CHI"; echo
    curl -s -m 60 -X POST "$CONTROLLORE/api/monitor?azione=avvia&chi=$CHI"; echo
fi
(( completo )) || exit 1
