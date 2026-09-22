#!/usr/bin/env python3
"""
Controlla che la pagina del monitor non referenzi elementi inesistenti.

`node --check` valida la sintassi ma non accorge che document.getElementById()
punti a un id che nel corpo della pagina non c'e' piu': e' un errore a runtime
che azzera l'intero script e lascia la pagina muta. Questo controllo lo trova.

    python3 tools/check_page.py [url]
"""
import re
import sys
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
html = urllib.request.urlopen(url, timeout=10).read().decode()

body = html.split("<body>", 1)[-1].split("<script>", 1)[0]
script = html.split("<script>", 1)[-1].rsplit("</script>", 1)[0]

present = set(re.findall(r'id="([A-Za-z0-9_]+)"', body))
# Gli id creati dinamicamente dal JS non stanno nell'HTML statico
dynamic = set(re.findall(r'id="([A-Za-z0-9_]+)_\$\{', script))

wanted = set(re.findall(r"getElementById\('([A-Za-z0-9_]+)'\)", script))
missing = {w for w in wanted
           if w not in present and not any(w.startswith(d + "_") for d in dynamic)}

print(f"url          : {url}")
print(f"id nel body  : {len(present)}")
print(f"id cercati   : {len(wanted)}")
if missing:
    print("\nERRORE: il JS cerca id che nel body non esistono:")
    for m in sorted(missing):
        print("  -", m)
    sys.exit(1)
print("\nOK: ogni getElementById ha il suo elemento.")
