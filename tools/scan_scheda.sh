#!/usr/bin/env bash
#
# Scan in soglia del self-trigger di UNA SCHEDA del controllore a schede
# ([settings] MultiBoard = true), a run in corso: un VX2730B, o un V1742
# lanciato con --board. Come scan_v1742.sh non ferma la DAQ: cambia le soglie
# scrivendo nel file di comando della scheda, e tutti i punti stanno nel run.
#
#   tools/scan_scheda.sh -b vx2730 -o "200 100 60 40 30 20 15" -s 20
#   tools/scan_scheda.sh -b vx2730 -c "0 1" -o "200 100 60 40 30 20 15" -s 20
#   tools/scan_scheda.sh -b vx2730 -u mV -o "20 10 6 4 3 2" -s 20
#
# Senza -c tutti i canali del trigger si muovono insieme, e il rate e' quello
# della logica della scheda (OR, AND...). Con -c un canale alla volta, gli
# altri alla soglia massima: serve l'OR. Per il VX2730B le soglie sono
# conteggi ADC dalla base; con -u mV sono mV nominali, convertiti in conteggi
# canale per canale col guadagno di ciascuno (cosi' "tutti insieme" vuol dire
# la stessa soglia in mV anche con guadagni diversi). A fine scan, anche
# interrotto, si rimettono quelle di partenza. JSON e grafico in plots/.
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLOTS="${PLOTS:-$ROOT/plots}"
TOML="${TOML:-$ROOT/config/run-local.toml}"
REGISTRO="${REGISTRO:-$ROOT/data/scan-in-corso.json}"
SCHEDA=""
VALORI=""
CANALI=""
SECONDI=20
UNITA=counts

uso() {
    sed -n '3,15p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    echo
    echo "Opzioni:"
    echo "  -b <scheda>     la sezione [boards.<scheda>] del TOML (obbligatoria)"
    echo "  -o \"<lista>\"    soglie da provare, dalla piu' alta"
    echo "  -c \"<canali>\"   un canale alla volta, questi (serve SelfTriggerLogic = OR)"
    echo "  -s <secondi>    durata di ogni punto (default: $SECONDI)"
    echo "  -u counts|mV    unita' delle soglie di -o (default: counts; mV solo per il VX2730B)"
    exit "${1:-0}"
}

while getopts "b:o:c:s:u:h" opt; do
    case "$opt" in
        b) SCHEDA="$OPTARG" ;;
        o) VALORI="$OPTARG" ;;
        c) CANALI="$OPTARG" ;;
        s) SECONDI="$OPTARG" ;;
        u) UNITA="$OPTARG" ;;
        h) uso 0 ;;
        *) uso 1 ;;
    esac
done
[[ -n "$SCHEDA" && -n "$VALORI" ]] || uso 1
[[ "$UNITA" == "counts" || "$UNITA" == "mV" ]] || { echo "ERRORE: -u vale counts o mV, non '$UNITA'." >&2; exit 1; }

# --- Controlli prima di partire ---------------------------------------------
# La DAQ di QUESTA scheda: con piu' schede ce n'e' una per scheda, e che ne
# giri un'altra non vuol dire niente.
PID_DAQ=$(pgrep -af "main/DAQ-WC" | awk -v s="$SCHEDA" '{for(i=1;i<NF;i++) if($i=="--board" && $(i+1)==s){print $1; exit}}')
if [[ -z "$PID_DAQ" ]]; then
    echo "ERRORE: la scheda $SCHEDA non sta acquisendo: questo scan cambia le soglie" >&2
    echo "a run in corso, quindi il run va avviato prima, con $SCHEDA spuntata." >&2
    exit 1
fi

# Dal TOML: la cartella dei dati (quella della scheda, o [digitizer]), il
# modello, e la logica del trigger. Il TOML e' quello con cui e' partita la
# DAQ, preso dalla sua riga di comando: un altro direbbe una cartella sbagliata.
TOML_DAQ=$(tr '\0' '\n' < /proc/"$PID_DAQ"/cmdline | grep -m1 '\.toml$' || true)
# Un percorso relativo vale dalla cartella della DAQ, non da questa.
[[ -n "$TOML_DAQ" && "$TOML_DAQ" != /* ]] && TOML_DAQ="$(readlink /proc/"$PID_DAQ"/cwd)/$TOML_DAQ"
[[ -n "$TOML_DAQ" ]] && TOML="$TOML_DAQ"
LETTO=$(python3 - "$TOML" "$SCHEDA" <<'EOF'
import sys, tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
t = (d.get("boards") or {}).get(sys.argv[2]) or {}
g = d.get("digitizer", {})
print(t.get("OutputDir") or g.get("OutputDir") or "")
modello = str(t.get("Model") or "V1742")
print(modello)
if modello.upper().startswith(("VX27", "V27", "DT27")):
    print(str(t.get("SelfTriggerLogic", "OR")))
    print(str(t.get("SelfTriggerMajority", "")))
else:
    print("OR"); print("")
EOF
) || { echo "ERRORE: non riesco a leggere $TOML" >&2; exit 1; }
DATI=$(sed -n 1p <<< "$LETTO")
MODELLO=$(sed -n 2p <<< "$LETTO")
LOGICA=$(sed -n 3p <<< "$LETTO")
MAGG=$(sed -n 4p <<< "$LETTO")
[[ -z "$DATI" ]] && DATI="$ROOT/data"

STATO="$DATI/live-status-$SCHEDA.json"
if [[ ! -f "$STATO" ]]; then
    echo "ERRORE: manca $STATO: il run di $SCHEDA non usa il self-trigger," >&2
    echo "o scrive in un'altra cartella." >&2
    exit 1
fi
# Uno stato piu' vecchio della DAQ e' di un run precedente.
AVVIO_DAQ=$(( $(date +%s) - $(ps -o etimes= -p "$PID_DAQ" | tr -d ' ') ))
if (( $(stat -c %Y "$STATO") < AVVIO_DAQ )); then
    echo "ERRORE: $STATO e' di un run precedente: il run in corso di $SCHEDA" >&2
    echo "non usa il self-trigger." >&2
    exit 1
fi
# Un canale alla volta vuol dire gli altri a soglia massima: con un AND, o una
# maggioranza, il trigger non scatterebbe piu' e ogni punto darebbe zero.
if [[ -n "$CANALI" && "${LOGICA^^}" != "OR" ]]; then
    echo "ERRORE: un canale alla volta serve SelfTriggerLogic = OR; $SCHEDA ha $LOGICA." >&2
    echo "Con $LOGICA si fa lo scan di tutti i canali insieme (senza -c)." >&2
    exit 1
fi

# --- La pagina di controllo deve sapere che c'e' uno scan -------------------
# Lo stesso file che scrive lei: cosi' mostra l'avanzamento, offre "Stop scan"
# (SIGTERM al gruppo, che noise_scan.py intercetta per rimettere le soglie) e
# non lascia avviare o fermare run mentre lo scan le usa. "script" e' il pezzo
# di percorso che la pagina cerca nella riga di comando di questo processo.
DESCR="$SCHEDA  $([[ -n "$CANALI" ]] && echo "one channel at a time ($CANALI)" || echo "all channels")  $VALORI$([[ "$UNITA" == mV ]] && echo " mV")"
python3 - "$REGISTRO" "$$" "$(ps -o pgid= $$ | tr -d ' ')" "$SECONDI" "$DESCR" <<'EOF'
import json, sys, time
f, pid, pgid, sec, valori = sys.argv[1:]
json.dump({"pid": int(pid), "pgid": int(pgid), "tipo": "scheda",
           "script": "tools/scan_scheda.sh", "valori": valori,
           "secondi": float(sec), "avviato": time.time()}, open(f, "w"))
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
fermato() {
    echo "scan fermato da fuori: soglie di partenza rimesse, run lasciato acceso."
    deregistra
    exit 143
}
trap fermato TERM INT
trap deregistra EXIT

STAMP=$(date +%Y%m%d_%H%M%S)
INIZIO=$(date +%s)
echo "=== Scan del self-trigger di $SCHEDA ($MODELLO) ==="
echo "dati    : $DATI"
echo "logica  : $LOGICA$([[ -n "$MAGG" && "${LOGICA^^}" == "MAJORITY" ]] && echo " $MAGG")"
echo "soglie  : $VALORI $([[ "$UNITA" == mV ]] && echo "mV nominali" || echo "conteggi")"
echo "canali  : $([[ -n "$CANALI" ]] && echo "uno alla volta: $CANALI" || echo "tutti insieme")"
echo "durata  : $SECONDI s per punto"
echo

ARGS=(--scheda "$SCHEDA" --offsets $VALORI --seconds "$SECONDI" -d "$DATI" -o "$PLOTS"
      --logica "$LOGICA")
[[ -n "$MAGG" ]] && ARGS+=(--maggioranza "$MAGG")
[[ "$UNITA" == "mV" ]] && ARGS+=(--mv)

json=()
completo=1
if [[ -n "$CANALI" ]]; then
    for ch in $CANALI; do
        echo "=== ch$ch ==="
        if ! python3 "$ROOT/tools/noise_scan.py" "${ARGS[@]}" --canale "$ch"; then
            echo "ERRORE: lo scan di ch$ch non e' arrivato in fondo: mi fermo qui." >&2
            completo=0
        fi
        f=$(ls -t "$PLOTS"/noise_scan_*_"$SCHEDA"_ch"$ch".json 2>/dev/null | head -1)
        [[ -n "$f" ]] && (( $(stat -c %Y "$f") >= INIZIO )) && json+=("$f")
        (( completo )) || break
        echo
    done
else
    python3 "$ROOT/tools/noise_scan.py" "${ARGS[@]}" || completo=0
    f=$(ls -t "$PLOTS"/noise_scan_*_"$SCHEDA".json 2>/dev/null | head -1)
    [[ -n "$f" ]] && (( $(stat -c %Y "$f") >= INIZIO )) && json+=("$f")
fi

if (( ${#json[@]} > 0 )); then
    echo
    echo "=== Grafico ==="
    PLOTARGS=(--logy)
    [[ "$UNITA" == "mV" ]] && PLOTARGS+=(--mv-nominali)
    python3 "$ROOT/tools/plot_scan.py" "${PLOTARGS[@]}" -o "$PLOTS/scan_${SCHEDA}_${STAMP}.png" "${json[@]}"
fi
deregistra
(( completo )) || exit 1
