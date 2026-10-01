#!/usr/bin/env bash
#
# Scan in soglia del CFD V812.
#
# A differenza del self-trigger, la soglia del V812 NON si cambia a run in
# corso: i suoi registri li scrive la DAQ all'avvio, e non c'e' nessun
# meccanismo a caldo. Ogni punto dello scan e' quindi una run separata:
# si riscrive il TOML, si lancia la DAQ per un tempo fisso, si conta.
#
# Il TOML viene rimesso com'era alla fine, anche se lo scan viene interrotto.
#
#   tools/scan_v812.sh                         # soglie di default
#   tools/scan_v812.sh -t "5 8 12 20 30" -s 60
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOML="$ROOT/config/run-local.toml"
SOGLIE="5 7 10 15 20 30"
SECONDI=60

uso() {
    sed -n '3,13p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    echo
    echo "Opzioni:"
    echo "  -t \"<lista>\"  soglie in mV da provare   (default: $SOGLIE)"
    echo "  -s <secondi>  durata di ogni punto      (default: $SECONDI)"
    echo "  -c <file>     TOML da usare             (default: $TOML)"
    exit "${1:-0}"
}

while getopts "t:s:c:h" opt; do
    case "$opt" in
        t) SOGLIE="$OPTARG" ;;
        s) SECONDI="$OPTARG" ;;
        c) TOML="$OPTARG" ;;
        h) uso 0 ;;
        *) uso 1 ;;
    esac
done

# --- Controlli prima di partire -------------------------------------------
# Questo scan la DAQ la lancia lui, una volta per punto: se ne trova una gia'
# viva si fermerebbero a vicenda sul bus.
if pgrep -f "main/DAQ-WC" > /dev/null; then
    echo "ERRORE: c'e' gia' una DAQ in esecuzione." >&2
    echo "Questo scan lancia una run per ogni punto: ferma quella in corso prima." >&2
    exit 1
fi

[[ -f "$TOML" ]] || { echo "ERRORE: $TOML non esiste." >&2; exit 1; }

# Il CFD deve essere acceso, se no lo scan misurerebbe il self-trigger
# credendo di misurare il V812.
python3 - "$TOML" <<'PY' || exit 1
import sys, tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
cfd = d.get("cfd", {})
dig = d.get("digitizer", {})
problemi = []
if not cfd.get("Enabled"):
    problemi.append("[cfd] Enabled non e' true: la DAQ non programmerebbe il V812")
if not dig.get("ExternalTrigger"):
    problemi.append("ExternalTrigger non e' true: l'OR del V812 arriva da TRG-IN")
if dig.get("SelfTrigger"):
    problemi.append("SelfTrigger e' true: i due trigger andrebbero in OR e il rate "
                    "misurato non sarebbe quello del V812")
if problemi:
    print("ERRORE nella configurazione:", file=sys.stderr)
    for p in problemi:
        print("  - " + p, file=sys.stderr)
    sys.exit(1)
PY

BACKUP="$TOML.bak-scan-$(date +%Y%m%d-%H%M%S)"
cp "$TOML" "$BACKUP"
# Il ripristino va fatto comunque: se lo scan si interrompe a meta', un TOML
# con la soglia dell'ultimo punto e' una trappola per la run successiva.
trap 'cp "$BACKUP" "$TOML"; echo; echo "TOML ripristinato da $BACKUP"' EXIT

# Durante lo scan comanda il tempo, non il conteggio.
sed -i -E "s|^NEvents *=.*|NEvents         = 100000000|" "$TOML"

STAMP=$(date +%Y%m%d_%H%M%S)
LAVORO=$(mktemp -d)
RISULTATI="$LAVORO/punti.jsonl"
: > "$RISULTATI"

echo "=== Scan in soglia del CFD V812 ==="
echo "soglie  : $SOGLIE  mV"
echo "durata  : $SECONDI s per punto"
echo "backup  : $BACKUP"
echo
printf "  %8s %10s %12s %14s\n" "soglia" "eventi" "durata [s]" "rate [Hz]"

for S in $SOGLIE; do
    sed -i -E "s|^Threshold  *=.*|Threshold   = [$S, $S]|" "$TOML"

    USCITA="$LAVORO/punto_$S.out"
    ( cd "$ROOT/build" && timeout "$SECONDI" ./main/DAQ-WC "$TOML" ) > "$USCITA" 2>&1 || true
    FINE=$(date +%s)

    RUNFILE=$(tr '\r' '\n' < "$USCITA" | sed 's/\x1b\[[0-9;]*m//g' \
              | grep -o 'HDF5 output path selected: .*' | tail -1 \
              | sed 's/HDF5 output path selected: //' | tr -d ' ') || true

    if [[ -z "$RUNFILE" ]]; then
        echo "  soglia $S mV: la DAQ non ha prodotto nessun file, salto" >&2
        tr '\r' '\n' < "$USCITA" | grep -i error | head -3 >&2 || true
        continue
    fi

    python3 - "$ROOT" "$RUNFILE" "$S" "$FINE" "$RISULTATI" <<'PY'
import json, os, sys
# python legge da stdin, quindi __file__ non esiste: la radice del progetto
# arriva da argv.
sys.path.insert(0, os.path.join(sys.argv[1], "tools"))
import daqio

runfile, soglia, fine, dest = sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
if not os.path.exists(runfile):
    runfile += ".gz"

# Una run chiusa dal timeout lascia il file con il flag SWMR aperto: live=True
# e' l'unico modo di rileggerla.
hdr, _ = daqio.load(runfile, last=1, live=not runfile.endswith(".gz"))
eventi = int(hdr["NEventsInFile"])

# La durata si misura da StartTime, scritto dalla DAQ quando apre il file,
# cioe' dopo la configurazione: usare il tempo totale del processo includerebbe
# i secondi di messa a punto e abbasserebbe il rate di un fattore che cambia
# da punto a punto.
durata = fine - float(hdr["StartTime"])
rate = eventi / durata if durata > 0 else 0.0

print("  %8.1f %10d %12.1f %14.3f" % (soglia, eventi, durata, rate))
with open(dest, "a") as f:
    f.write(json.dumps({"soglia_mv": soglia, "eventi": eventi,
                        "durata_s": round(durata, 2), "rate": round(rate, 4),
                        "file": os.path.basename(runfile)}) + "\n")
PY
done

# --- JSON riassuntivo ------------------------------------------------------
mkdir -p "$ROOT/plots"
JSON="$ROOT/plots/scan_v812_$STAMP.json"
python3 - "$TOML" "$BACKUP" "$RISULTATI" "$JSON" "$SECONDI" <<'PY'
import json, sys, time, tomllib
toml_corrente, backup, punti_path, dest, secondi = sys.argv[1:6]
cfd = tomllib.load(open(backup, "rb")).get("cfd", {})
punti = [json.loads(r) for r in open(punti_path) if r.strip()]
rec = {
    "tipo": "v812",
    "quando": time.strftime("%Y-%m-%d %H:%M:%S"),
    "base_address": hex(cfd.get("BaseAddress", 0)),
    "canali": cfd.get("Channels", []),
    "width": cfd.get("Width"),
    "dead_time": cfd.get("DeadTime"),
    "secondi_per_punto": float(secondi),
    "punti": punti,
}
json.dump(rec, open(dest, "w"), indent=2)
print("\nmisura : %s  (%d punti)" % (dest, len(punti)))
PY

echo
echo "=== Grafico ==="
python3 "$ROOT/tools/plot_scan.py" "$JSON"
