#!/usr/bin/env bash
#
# Scan in soglia del SELF-TRIGGER del V1742.
#
# La soglia del self-trigger si cambia a run in corso, scrivendo nel file di
# comando live-threshold.txt: lo scan quindi NON ferma la DAQ, e tutti i punti
# stanno dentro la stessa run. E' il motivo per cui questo script e quello del
# V812 hanno forma diversa.
#
#   tools/scan_v1742.sh                        # offset di default
#   tools/scan_v1742.sh -o "3 4 5 6 8 10" -s 30
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OFFSETS="3 4 5 6 7 8 10 12"
SECONDI=20
SIGMA=""

uso() {
    sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    echo
    echo "Opzioni:"
    echo "  -o \"<lista>\"  offset da provare          (default: $OFFSETS)"
    echo "  -s <secondi>  durata di ogni punto       (default: $SECONDI)"
    echo "  -g <sigma>    rumore assunto in conteggi (default: stimato dai dati)"
    exit "${1:-0}"
}

while getopts "o:s:g:h" opt; do
    case "$opt" in
        o) OFFSETS="$OPTARG" ;;
        s) SECONDI="$OPTARG" ;;
        g) SIGMA="$OPTARG" ;;
        h) uso 0 ;;
        *) uso 1 ;;
    esac
done

# --- Controlli prima di partire -------------------------------------------
# Senza una run in corso lo scan non ha su cosa lavorare: le soglie si
# cambiano a caldo, quindi la DAQ deve essere viva.
if ! pgrep -f "main/DAQ-WC" > /dev/null; then
    echo "ERRORE: non c'e' nessuna DAQ in esecuzione." >&2
    echo "Questo scan cambia le soglie a run in corso: la run devi lanciarla prima." >&2
    exit 1
fi

STATO="$ROOT/data/live-status.json"
if [[ ! -f "$STATO" ]]; then
    echo "ERRORE: manca $STATO." >&2
    echo "Lo pubblica la DAQ solo con SelfTrigger = true: controlla il TOML." >&2
    exit 1
fi

# Uno stato vecchio di ore e' di una run precedente, e lo scan finirebbe per
# attribuire i rate alle soglie sbagliate.
ETA=$(( $(date +%s) - $(stat -c %Y "$STATO") ))
if (( ETA > 600 )); then
    echo "ERRORE: live-status.json non viene aggiornato da $ETA secondi." >&2
    echo "Probabilmente e' di una run precedente, oppure gira con SelfTrigger = false." >&2
    exit 1
fi

echo "=== Scan in soglia del self-trigger V1742 ==="
echo "offset  : $OFFSETS"
echo "durata  : $SECONDI s per punto"
echo

ARGS=(--offsets $OFFSETS --seconds "$SECONDI")
[[ -n "$SIGMA" ]] && ARGS+=(--sigma "$SIGMA")

python3 "$ROOT/tools/noise_scan.py" "${ARGS[@]}"

# --- Grafico ---------------------------------------------------------------
ULTIMO=$(ls -t "$ROOT"/plots/noise_scan_*.json 2>/dev/null | head -1 || true)
if [[ -z "$ULTIMO" ]]; then
    echo "Nessun JSON prodotto: salto il grafico." >&2
    exit 1
fi

echo
echo "=== Grafico ==="
python3 "$ROOT/tools/plot_scan.py" "$ULTIMO"
echo
echo "Per sovrapporre questo scan a uno del V812:"
echo "  python3 tools/plot_scan.py $ULTIMO plots/scan_v812_<data>.json"
