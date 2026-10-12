#!/usr/bin/env bash
#
# Aggiorna l'e-log su Google con le run della macchina DAQ.
#
#   tools/elog_sync.sh              # le ultime 10 run
#   tools/elog_sync.sh -n 50        # le ultime 50
#   tools/elog_sync.sh --tutte      # tutte, prove comprese
#
# Gira SUL MAC: le credenziali del foglio stanno qui e una chiave di servizio
# non si copia sulla macchina DAQ. I dati stanno di la', quindi il CSV lo
# genera la macchina DAQ e viaggia via ssh -- non e' una sincronizzazione di
# codice, quindi la regola "solo git" non c'entra.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHIAVE="${ELOG_CHIAVE:-1cM8O6Co1Llx5Ysa4BfS4rmMqa4XEYJpdjSv61xerp44}"
REMOTO="${ELOG_REMOTO:-daq-pc}"
REPO_REMOTO="${ELOG_REPO:-/home/daq/daq-standalone}"
ULTIME=10

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--ultime) ULTIME="$2"; shift 2 ;;
        --tutte)     ULTIME=""; shift ;;
        -h|--help)   sed -n '3,9p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *) echo "opzione sconosciuta: $1" >&2; exit 1 ;;
    esac
done

# Serve l'ambiente con gspread. Si cerca dove lo abbiamo messo, e se non c'e'
# si dice come farlo invece di fallire con un ImportError.
PY="${ELOG_PYTHON:-}"
if [[ -z "$PY" ]]; then
    for c in "$ROOT/.venv-elog/bin/python" "$(command -v python3)"; do
        if "$c" -c "import gspread" 2>/dev/null; then PY="$c"; break; fi
    done
fi
if [[ -z "$PY" ]]; then
    echo "Manca gspread. Una volta sola:" >&2
    echo "  python3 -m venv $ROOT/.venv-elog && $ROOT/.venv-elog/bin/pip install gspread google-auth" >&2
    exit 1
fi

CSV=$(mktemp /tmp/runs_auto.XXXXXX.csv)
trap 'rm -f "$CSV"' EXIT

OPZ=""
[[ -n "$ULTIME" ]] && OPZ="-n $ULTIME"
echo "→ genero le righe sulla macchina DAQ${ULTIME:+ (ultime $ULTIME run)}"
ssh "$REMOTO" "cd $REPO_REMOTO && python3 tools/elog.py $OPZ" > "$CSV"
echo "  $(( $(wc -l < "$CSV") - 1 )) run"

echo "→ verso nel foglio"
"$PY" "$ROOT/tools/elog_push.py" "$CSV" --chiave "$CHIAVE" "$@"

echo "https://docs.google.com/spreadsheets/d/$CHIAVE/edit"
