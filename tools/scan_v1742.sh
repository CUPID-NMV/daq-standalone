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

# La cartella dei dati e' quella che la DAQ dichiara nel TOML, non "data"
# cablata: da quando OutputDir si cambia dalla pagina di controllo, cercare
# qui dentro vorrebbe dire guardare la cartella sbagliata e concludere che la
# DAQ non sta pubblicando lo stato.
TOML="${TOML:-$ROOT/config/run-local.toml}"
# Due righe: la cartella e SelfTrigger. Si leggono insieme perche' servono
# insieme, e perche' un secondo tomllib.load sullo stesso file e' fiato sprecato.
LETTO=$(python3 - "$TOML" <<'EOF'
import sys
try:
    import tomllib
    with open(sys.argv[1], "rb") as f:
        d = tomllib.load(f).get("digitizer", {})
    print(d.get("OutputDir") or "")
    st = d.get("SelfTrigger")
    print("" if st is None else ("true" if st else "false"))
except Exception:
    print(); print("")   # niente TOML, niente chiavi: ci pensano i ripieghi sotto
EOF
)
DATI=$(sed -n 1p <<< "$LETTO")
SELFTRIG=$(sed -n 2p <<< "$LETTO")
[[ -z "$DATI" ]] && DATI="$ROOT/data"

# SelfTrigger spento non e' "forse": e' la risposta. Questo scan muove la
# soglia del self-trigger e misura il rate che ne esce; con il trigger esterno
# il rate lo detta il LED e ogni punto darebbe lo stesso numero. Prima qui si
# arrivava all'eta' di live-status.json e si proponevano DUE cause possibili,
# lasciando a chi legge il lavoro di capire quale -- mentre il TOML ce l'ha
# scritto sopra.
if [[ "$SELFTRIG" == "false" ]]; then
    echo "ERRORE: nel TOML SelfTrigger = false." >&2
    echo "  $TOML" >&2
    echo "La run in corso non usa il self-trigger, quindi non pubblica" >&2
    echo "live-status.json e non c'e' nessuna soglia da far scorrere." >&2
    echo "Per questo scan serve una run con SelfTrigger = true." >&2
    exit 1
fi

STATO="$DATI/live-status.json"
if [[ ! -f "$STATO" ]]; then
    echo "ERRORE: manca $STATO." >&2
    echo "Con SelfTrigger = true la DAQ lo scrive dentro OutputDir: se non c'e'," >&2
    echo "la run in corso sta scrivendo in un'altra cartella." >&2
    exit 1
fi

# Uno stato vecchio di ore e' di una run precedente, e lo scan finirebbe per
# attribuire i rate alle soglie sbagliate.
ETA=$(( $(date +%s) - $(stat -c %Y "$STATO") ))
if (( ETA > 600 )); then
    echo "ERRORE: live-status.json non viene aggiornato da $ETA secondi." >&2
    echo "  $STATO" >&2
    echo "Nel TOML SelfTrigger = ${SELFTRIG:-?}, quindi il file e' di una run" >&2
    echo "precedente: quella in corso scrive altrove, o e' partita con un TOML" >&2
    echo "diverso da questo. Controlla OutputDir." >&2
    exit 1
fi

echo "=== Scan in soglia del self-trigger V1742 ==="
echo "offset  : $OFFSETS"
echo "durata  : $SECONDI s per punto"
echo

ARGS=(--offsets $OFFSETS --seconds "$SECONDI" --data-dir "$DATI")
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
