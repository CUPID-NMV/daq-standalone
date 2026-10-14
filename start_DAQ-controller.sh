#!/usr/bin/env bash
#
# Avvia (o riavvia) la pagina di controllo della DAQ.
#
#   ./start_DAQ-controller.sh              # avvia, o riavvia se gira gia'
#   ./start_DAQ-controller.sh --stato      # dice solo come sta, non tocca niente
#   ./start_DAQ-controller.sh --ferma      # lo ferma e basta
#   ./start_DAQ-controller.sh -p 8799      # su un'altra porta, per le prove
#   ./start_DAQ-controller.sh -- --psu-host 127.0.0.1
#                                       # tutto quello dopo "--" va a daq_control.py
#
# Il monitor NON lo avvia: conviene accenderlo da dentro la pagina (Start
# monitor), cosi' si aggancia da solo a OutputDir, che si cambia dalla pagina.
# Avviato da fuori resta disallineato il giorno che si cambia cartella.
#
# Perche' uno script invece di una riga di comando: la riga giusta e'
# "python3 tools/daq_control.py -b 0.0.0.0", che e' facile da dimenticare e
# non controlla niente. Qui prima di ammazzare un processo si guarda se c'e'
# una run in corso, si verifica che il processo sia davvero il nostro, e alla
# fine si controlla che la pagina risponda per davvero invece di dire "fatto".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTA=8766
BIND="0.0.0.0"
AZIONE="avvia"
FORZA=0
EXTRA=()          # quello che sta dopo "--" va a daq_control.py
LOG="$ROOT/data/controllore.log"

uso() { sed -n '3,15p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--porta|--port) PORTA="$2"; shift 2 ;;
        -b|--bind)         BIND="$2"; shift 2 ;;
        --stato)           AZIONE="stato"; shift ;;
        --ferma|--stop)    AZIONE="ferma"; shift ;;
        -f|--forza)        FORZA=1; shift ;;
        -h|--help)         uso 0 ;;
        --) shift; EXTRA=("$@"); break ;;
        *) echo "opzione sconosciuta: $1" >&2; uso 1 ;;
    esac
done

# --- chi tiene la porta ----------------------------------------------------
# Mai "pkill -f daq_control": un pkill su riga di comando ha gia' ucciso in
# questo progetto la shell che lo eseguiva insieme al processo voluto. Si
# parte dalla PORTA, si risale al PID, e si manda il segnale a quel PID.
pid_sulla_porta() {
    local p
    if command -v ss > /dev/null; then
        p=$(ss -lptn "sport = :$PORTA" 2>/dev/null |
            grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
    fi
    if [[ -z "${p:-}" ]] && command -v lsof > /dev/null; then
        p=$(lsof -tiTCP:"$PORTA" -sTCP:LISTEN 2>/dev/null | head -1)
    fi
    echo "${p:-}"
}

# Che quel PID sia davvero il controllore e non un altro programma che per caso
# usa la stessa porta: si ammazza solo quello che si e' riconosciuto.
e_il_controllore() {
    local pid="$1" riga
    [[ -z "$pid" ]] && return 1
    # /proc e' Linux; ps funziona anche altrove. Prima il piu' preciso.
    riga=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    [[ -z "$riga" ]] && riga=$(ps -p "$pid" -o args= 2>/dev/null || true)
    [[ "$riga" == *daq_control.py* ]]
}

PID=$(pid_sulla_porta)

# --- stato ------------------------------------------------------------------
risposta=""
if [[ -n "$PID" ]]; then
    risposta=$(curl -s -m 5 "http://127.0.0.1:$PORTA/api/stato" 2>/dev/null || true)
fi

descrivi() {
    if [[ -z "$PID" ]]; then
        echo "controllore : spento (nessuno in ascolto sulla $PORTA)"
        return
    fi
    if ! e_il_controllore "$PID"; then
        echo "controllore : la porta $PORTA e' occupata dal PID $PID, che NON e'"
        echo "              daq_control.py. Non lo tocco: guarda cos'e' con"
        echo "              'ps -p $PID -o args='"
        return
    fi
    echo "controllore : attivo, PID $PID, in ascolto sulla $PORTA"
    echo "              da $(ps -o etime= -p "$PID" 2>/dev/null | tr -d ' ')"
    if [[ -n "$risposta" ]]; then
        python3 - "$risposta" <<'EOF'
import json, sys
d = json.loads(sys.argv[1])
c = d.get("coda") or {}
voci = c.get("voci") or []
# "coda" e' un DIZIONARIO con dentro la lista "voci", non la lista: contare
# len(coda) dava 4 -- le sue chiavi -- e annunciava quattro run in attesa su
# una coda finita. Lo stato di una voce da fare e' pending o running; done e
# interrupted sono passato.
da_fare = [v for v in voci if v.get("stato") in ("pending", "running")]
print("run in corso: %s" % ("SI" if d.get("in_corso") else "no"))
print("coda        : %s, %d da fare su %d voci"
      % ("attiva" if c.get("attiva") else "ferma", len(da_fare), len(voci)))
EOF
    else
        echo "              ATTENZIONE: non risponde su /api/stato"
    fi
}

if [[ "$AZIONE" == "stato" ]]; then
    descrivi
    exit 0
fi

# --- fermare ----------------------------------------------------------------
# Il riavvio uccide i thread della coda: una sequenza di run in corso si
# interrompe e non riparte da sola. E' gia' successo, quindi qui si chiede,
# invece di scoprirlo dopo.
ferma() {
    [[ -z "$PID" ]] && return 0
    if ! e_il_controllore "$PID"; then
        echo "La porta $PORTA e' occupata dal PID $PID, che non e' il controllore." >&2
        echo "Non lo ammazzo. Guarda cos'e': ps -p $PID -o args=" >&2
        exit 1
    fi

    if [[ "$FORZA" != "1" && -n "$risposta" ]]; then
        occupato=$(python3 -c "
import json,sys
d=json.loads(sys.argv[1])
c=d.get('coda') or {}
da_fare=[v for v in (c.get('voci') or []) if v.get('stato') in ('pending','running')]
print('%s|%d' % ('si' if d.get('in_corso') else 'no', len(da_fare)))" "$risposta")
        in_corso="${occupato%%|*}"; in_coda="${occupato##*|}"
        if [[ "$in_corso" == "si" || "$in_coda" != "0" ]]; then
            echo
            echo "ATTENZIONE: c'e' una run in corso o una coda in attesa."
            echo "  run in corso : $in_corso"
            echo "  coda         : $in_coda in attesa"
            echo
            echo "Il riavvio del controllore ferma la coda, che NON riparte da sola."
            read -r -p "Fermo lo stesso? [s/N] " rr
            [[ "$rr" =~ ^[sSyY]$ ]] || { echo "lasciato com'era."; exit 1; }
        fi
    fi

    echo "fermo il controllore (PID $PID)..."
    kill -TERM "$PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 0.5
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo "non si ferma con TERM, mando KILL"
        kill -KILL "$PID" 2>/dev/null || true
        sleep 1
    fi
    # La porta puo' restare occupata qualche istante dopo la morte del
    # processo: ripartire subito darebbe "Address already in use".
    for _ in $(seq 1 20); do
        [[ -z "$(pid_sulla_porta)" ]] && break
        sleep 0.5
    done
    PID=""
}

if [[ "$AZIONE" == "ferma" ]]; then
    ferma
    echo "fermato."
    exit 0
fi

# --- avviare ----------------------------------------------------------------
ferma

# Se la copia locale e' indietro, il riavvio non prende le modifiche che uno
# credeva di aver messo in opera. Si dice, non si fa il pull da soli: tirare
# codice nuovo mentre si riavvia e' un modo per cambiare due cose insieme.
if git -C "$ROOT" rev-parse --git-dir > /dev/null 2>&1; then
    if git -C "$ROOT" fetch -q origin 2>/dev/null; then
        indietro=$(git -C "$ROOT" rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
        if [[ "${indietro:-0}" != "0" ]]; then
            echo
            echo "NOTA: la copia locale e' indietro di $indietro commit rispetto a origin/main."
            echo "      Se stai riavviando per prendere una modifica, prima: git pull"
            echo
        fi
    fi
fi

mkdir -p "$(dirname "$LOG")"
echo "avvio: python3 tools/daq_control.py -b $BIND -p $PORTA"
# setsid + nohup: la pagina deve sopravvivere alla chiusura del terminale e
# alla caduta della rete, che e' tutto il motivo per cui gira sul PC DAQ.
setsid nohup python3 "$ROOT/tools/daq_control.py" -b "$BIND" -p "$PORTA" \
    ${EXTRA[@]+"${EXTRA[@]}"} >> "$LOG" 2>&1 < /dev/null &

# Non si dice "avviato" perche' il comando e' partito: si aspetta che risponda.
for _ in $(seq 1 30); do
    sleep 0.5
    if curl -s -m 2 "http://127.0.0.1:$PORTA/api/stato" > /dev/null 2>&1; then
        PID=$(pid_sulla_porta)
        echo
        echo "pronto. PID $PID"
        echo "  da qui          : http://127.0.0.1:$PORTA/"
        ip=$(hostname -I 2>/dev/null | awk '{print $1}')
        [[ -n "$ip" ]] && echo "  dalla rete      : http://$ip:$PORTA/"
        echo "  log             : $LOG"
        echo
        echo "Il monitor si accende da dentro la pagina (Start monitor)."
        exit 0
    fi
done

echo "NON risponde dopo 15 s. Ultime righe del log:" >&2
tail -15 "$LOG" >&2
exit 1
