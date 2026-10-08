#!/usr/bin/env python3
"""
Controlla che la pagina del monitor non referenzi elementi inesistenti.

`node --check` valida la sintassi ma non accorge che document.getElementById()
punti a un id che nel corpo della pagina non c'e' piu': e' un errore a runtime
che azzera l'intero script e lascia la pagina muta. Questo controllo lo trova.

    python3 tools/check_page.py [url]
"""
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
html = urllib.request.urlopen(url, timeout=10).read().decode()

body = html.split("<body>", 1)[-1].split("<script>", 1)[0]
script = html.split("<script>", 1)[-1].rsplit("</script>", 1)[0]

present = set(re.findall(r'id="([A-Za-z0-9_-]+)"', body))
# Gli id creati dinamicamente dal JS non stanno nell'HTML statico
dynamic = set(re.findall(r'id="([A-Za-z0-9_-]+)_\$\{', script))

# Apici singoli o doppi: cercare solo i primi faceva passare a vuoto una
# pagina intera scritta con i secondi.
wanted = set(re.findall(r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]""", script))

# Molte pagine si definiscono una scorciatoia, tipo
#     const $ = id => document.getElementById(id);
# e poi scrivono $("badge"). Senza riconoscerla, qui non si vedrebbe nessun
# riferimento e il controllo direbbe OK senza aver guardato niente.
for alias in re.findall(r"(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
                        r"\w+\s*=>\s*document\.getElementById", script):
    wanted |= set(re.findall(r"%s\(\s*['\"]([A-Za-z0-9_-]+)['\"]\s*\)"
                             % re.escape(alias), script))

missing = {w for w in wanted
           if w not in present and not any(w.startswith(d + "_") for d in dynamic)}

print(f"url          : {url}")
print(f"id nel body  : {len(present)}")
print(f"id cercati   : {len(wanted)}")

# Un controllo che non trova niente da controllare non e' un controllo
# passato: e' un controllo che non ha guardato. E' successo, e la pagina
# aveva comunque bisogno di essere verificata a mano.
if present and not wanted:
    print("\nERRORE: nel body ci sono id ma nel JS non si vede nessun "
          "riferimento.\nQuasi certamente la pagina usa una forma che questo "
          "controllo non riconosce: va esteso, non ignorato.")
    sys.exit(1)

if missing:
    print("\nERRORE: il JS cerca id che nel body non esistono:")
    for m in sorted(missing):
        print("  -", m)
    sys.exit(1)
print("\nOK: ogni getElementById ha il suo elemento.")

# --- sintassi del JavaScript -----------------------------------------------
# Serve davvero: la pagina e' generata da una stringa Python, e una barra
# rovescia interpretata da Python invece che dal browser trasforma "\n" in un
# a capo vero dentro una stringa JS. Quella e' una stringa non terminata: lo
# script intero non parte e la pagina resta muta, senza che niente lo dica.
node = shutil.which("node")
if not node:
    print("\nATTENZIONE: node non c'e', la sintassi del JavaScript non e' stata"
          " controllata.")
    sys.exit(0)

with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
    f.write(script)
    tmp = f.name
esito = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
if esito.returncode != 0:
    print("\nERRORE: il JavaScript della pagina non e' valido.")
    print(esito.stderr.strip()[:800])
    sys.exit(1)
print("OK: il JavaScript e' sintatticamente valido.")

def senza_testo(js):
    """Il JS senza commenti e senza stringhe.

    Senza questo, una parola seguita da una parentesi dentro un commento
    italiano -- "puo' essere inaccessibile (finestra privata)" -- passa per una
    chiamata a funzione e il controllo si riempie di falsi allarmi.
    """
    out, i, n = [], 0, len(js)
    while i < n:
        c = js[i]
        if c == "/" and i + 1 < n and js[i + 1] == "/":
            i = js.find("\n", i)
            if i < 0:
                break
        elif c == "/" and i + 1 < n and js[i + 1] == "*":
            i = js.find("*/", i)
            i = n if i < 0 else i + 2
        elif c in "\"'`":
            i += 1
            while i < n and js[i] != c:
                i += 2 if js[i] == "\\" else 1
            i += 1
            out.append('""')     # al posto della stringa, qualcosa di inerte
        else:
            out.append(c)
            i += 1
    return "".join(out)


# --- funzioni chiamate ma mai definite -------------------------------------
# node --check non se ne accorge: una funzione inesistente e' un errore solo
# quando la riga viene eseguita. Succede a meta' del giro di aggiornamento,
# con le immagini gia' rinfrescate, quindi la pagina sembra viva e si limita a
# mostrare l'avviso del catch. E' costato un pomeriggio: il blocco della lente
# era sparito riscrivendo i controlli per canale, e la pagina accusava la rete.
definite = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)", script))
definite |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", script))
# Un nome preceduto da punto e' un metodo di qualcun altro, non roba nostra.
chiamate = set(re.findall(r"(?<![.\w$])([a-z][A-Za-z0-9_$]*)\s*\(",
                          senza_testo(script)))
GLOBALI = {"if", "for", "while", "switch", "catch", "return", "function",
           "typeof", "new", "await", "async", "fetch", "parseInt",
           "parseFloat", "isNaN", "setInterval", "setTimeout", "alert",
           "confirm", "encodeURIComponent", "decodeURIComponent", "require"}
ignote = sorted(chiamate - definite - GLOBALI)
print(f"funzioni definite : {len(definite)}")
if ignote:
    print("\nERRORE: il JS chiama funzioni che non definisce:")
    for f in ignote:
        print("  -", f)
    sys.exit(1)
print("OK: ogni funzione chiamata e' definita.")
