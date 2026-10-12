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
PASSA=()          # opzioni da girare a elog_push.py

# Le opzioni sconosciute si rifiutano invece di passarle a valle: accettare
# tutto maschera i refusi, e qui un refuso puo' voler dire "--rifai".
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--ultime) ULTIME="$2"; shift 2 ;;
        --tutte)     ULTIME=""; shift ;;
        --rifai)     PASSA+=(--rifai); shift ;;
        -h|--help)   sed -n '3,9p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
        *) echo "opzione sconosciuta: $1" >&2; exit 1 ;;
    esac
done

# Dove girano i dati: qui o di la'. Lo stesso comando deve funzionare da tutte
# e due le macchine -- lanciato sul PC DAQ cercava "daq-pc", che e' un alias
# del solo Mac, e falliva sulla risoluzione del nome.
if [[ -d "$ROOT/data" ]] && compgen -G "$ROOT/data/*.log" > /dev/null; then
    LOCALE=1          # siamo sulla macchina che acquisisce
else
    LOCALE=0
fi

# Serve l'ambiente con gspread. Si cerca dove lo abbiamo messo sulle due
# macchine, e se non c'e' si dice come farlo invece di fallire con un
# ImportError.
PY="${ELOG_PYTHON:-}"
if [[ -z "$PY" ]]; then
    for c in "$ROOT/.venv-elog/bin/python" "$HOME/.venv-elog/bin/python" \
             "$(command -v python3)"; do
        if [[ -x "$c" ]] && "$c" -c "import gspread" 2>/dev/null; then PY="$c"; break; fi
    done
fi

# Le credenziali stanno in posti diversi sulle due macchine.
CRED="${ELOG_CRED:-}"
if [[ -z "$CRED" ]]; then
    for c in "$HOME/.config/daq-elog/credentials.json" \
             "$HOME/Downloads/.google_credentials.json"; do
        [[ -f "$c" ]] && { CRED="$c"; break; }
    done
fi
if [[ -z "$CRED" ]]; then
    echo "Non trovo le credenziali del foglio (ne' in ~/.config/daq-elog/ ne' in ~/Downloads/)." >&2
    exit 1
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
if [[ "$LOCALE" = "1" ]]; then
    echo "→ genero le righe qui${ULTIME:+ (ultime $ULTIME run)}"
    python3 "$ROOT/tools/elog.py" $OPZ > "$CSV"
else
    echo "→ genero le righe su $REMOTO${ULTIME:+ (ultime $ULTIME run)}"
    ssh "$REMOTO" "cd $REPO_REMOTO && python3 tools/elog.py $OPZ" > "$CSV"
fi
echo "  $(( $(wc -l < "$CSV") - 1 )) run"

echo "→ verso nel foglio"
"$PY" "$ROOT/tools/elog_push.py" "$CSV" --chiave "$CHIAVE" --credenziali "$CRED" \
    ${PASSA[@]+"${PASSA[@]}"}

echo "https://docs.google.com/spreadsheets/d/$CHIAVE/edit"
