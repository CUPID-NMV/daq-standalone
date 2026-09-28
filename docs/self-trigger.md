# Self-trigger del V1742 — how to

Il V1742 può generare il trigger da solo, dall'OR logico dei canali di
ingresso che superano una soglia. Questo documento spiega come si usa e come è
implementato, in quest'ordine.

---

## 1. Usarlo

Tutto si configura nel TOML. Il minimo indispensabile:

```toml
TriggerPolarity          = 1          # 0 = rising, 1 = falling (impulsi negativi)
SelfTrigger              = true
SelfTriggerMode          = "global"   # oppure "paired"
SelfTriggerChannels      = [8, 9]     # se omesso: tutti quelli di ChannelList
SelfTriggerThresholdMode = "relative"
SelfTriggerThresholdOffset = 20       # conteggi ADC sotto il piedistallo
```

`SelfTrigger` ed `ExternalTrigger` sono indipendenti: si possono accendere
entrambi e le due sorgenti vanno in OR.

### I due modi

| | cosa fa | requisiti |
|---|---|---|
| `"paired"` | le coppie di gruppi gr0/gr1 e gr2/gr3 acquisiscono in modo indipendente | sempre disponibile (AMC ≥ 0.4) |
| `"global"` | un canale sopra soglia fa acquisire **tutti** i gruppi | firmware ≥ 4.30_1.08 |

`HasGlobalTriggerFirmware()` controlla le revisioni ROC e AMC, ma **non ripiega
automaticamente**: se il firmware è vecchio stampa un warning e prosegue, e la
board può semplicemente non triggerare. Se non vedi eventi, questo è il primo
posto dove guardare nel log.

Attenzione anche al default: il template mette `"global"`, ma se la chiave
manca del tutto il codice usa `"paired"`.

### Frequenza di campionamento

La latenza del self-trigger non è compatibile con tutte le frequenze, e
`ConfigureSelfTrigger()` avvisa nei due casi problematici:

| campionamento | finestra | `paired` (~320 ns) | `global` (~420 ns) |
|---|---|---|---|
| 5 GHz | 205 ns | no | no |
| 2.5 GHz | 410 ns | **sì** | no |
| 1 GHz | 1024 ns | sì | sì |
| 750 MHz | 1365 ns | sì | sì |

Sono warning, non errori: la run parte comunque, e se la combinazione è
sbagliata si acquisiscono eventi senza l'impulso dentro. La configurazione
usata in pratica è **2.5 GHz in modo `paired`**, verificata: impulso a 50.0 ns
dall'inizio della finestra.

### I due modi di esprimere la soglia

- **`"absolute"`** — `SelfTriggerThreshold` è in conteggi ADC assoluti (0…4095).
  Una lista, un valore per canale; l'ultimo viene replicato sui rimanenti.

- **`"relative"`** — è quello che si usa quasi sempre. La DAQ misura il
  piedistallo in Transparent Mode all'avvio e mette la soglia a
  `SelfTriggerThresholdOffset` conteggi di distanza, nel verso indicato da
  `TriggerPolarity` (falling → sotto il piedistallo, rising → sopra).
  `SelfTriggerThresholdOffset` accetta uno scalare o una lista per canale.

Il modo relativo è preferibile perché il piedistallo si sposta fra una run e
l'altra: a parità di offset, la distanza vera resta quella richiesta.

### Cambiare le soglie a run in corso

Senza fermare l'acquisizione, si scrive nel file di comando:

```bash
printf "8 4\n9 6\n" > data/live-threshold.txt
```

Una riga per canale, `<canale> <offset>`. La DAQ se ne accorge entro pochi
eventi, riscrive le soglie e pubblica lo stato in `data/live-status.json`, da
cui il monitor e gli script di scan leggono offset, soglia e piedistallo
correnti. Un file rimasto da una run precedente viene ignorato: all'avvio se ne
registra il timestamp senza applicarlo.

`tools/noise_scan.py` è costruito su questo meccanismo: per ogni punto scrive
l'offset, aspetta la conferma nello stato, misura il rate.

---

## 2. Com'è implementato

Tutto sta in `src/Digitizer.cpp`. Il punto d'ingresso è `ConfigureTrigger()`,
chiamato una volta prima dell'acquisizione.

### La sequenza

```
ConfigureTrigger()
  ├── parte da una board "quieta": TRG-IN disabilitato + ClearSelfTrigger()
  ├── se SelfTrigger:  ConfigureSelfTrigger()
  │     ├── ComputeSelfTriggerThresholds()   ← misura il piedistallo (modo relative)
  │     ├── ApplySelfTriggerThresholds()     ← scrive soglie e maschere
  │     ├── 0x8000: routing e acquisizione automatica
  │     └── 0x810C: gruppi ammessi al trigger globale
  ├── abilita TRG-IN se ExternalTrigger
  ├── DumpSelfTriggerRegisters()             ← rilegge tutto e lo stampa
  └── WriteStatusFile()                      ← pubblica lo stato per il monitor
```

Il primo passo non è cosmetico: la misura del piedistallo in Transparent Mode
deve vedere **solo** i trigger software, quindi tutto il resto va spento prima.

### I registri

Definiti come costanti in `src/Digitizer.h`:

| registro | cosa | bit |
|---|---|---|
| `0x1n80` | soglia del canale, un gruppo per volta | `[11:0]` soglia, `[15:12]` indice del canale **nel gruppo** |
| `0x1nA8` | maschera dei canali del gruppo ammessi all'OR | un bit per canale |
| `0x8000` | Board Configuration | `[13]` Transparent Mode, `[21]` disabilita l'acquisizione automatica del gruppo, `[31:28]` routing del segnale di over-threshold |
| `0x8004` / `0x8008` | set / clear dei bit di `0x8000` | si scrive sempre attraverso questi, mai su `0x8000` |
| `0x810C` | Global Trigger Mask | `[3:0]` gruppi ammessi a generare il trigger globale |

Due dettagli che costano tempo se non si sanno:

- in `0x1n80` l'indice del canale è **relativo al gruppo** (0–7), non assoluto;
  `ApplyChannelThreshold()` fa la conversione.
- le maschere `0x1nA8` vengono scritte su **tutti** i gruppi, anche quelli non
  usati, perché un gruppo con la maschera sporca da una configurazione
  precedente continuerebbe a triggerare.

### La misura del piedistallo (modo `relative`)

`ComputeSelfTriggerThresholds()` fa così:

1. disabilita le correzioni DRS4;
2. mette la board in Transparent Mode (`SetTransparentMode(true)`);
3. chiama `MeasureBaseline()`, che acquisisce 10 eventi con trigger software e
   ne ricava media e RMS per canale;
4. torna in Output Mode;
5. calcola `soglia = piedistallo ∓ offset` secondo `TriggerPolarity`.

Se la misura fallisce si ripiega sui valori assoluti di
`SelfTriggerThreshold`, che esiste apposta come rete di sicurezza.

**Le correzioni DRS4 non vengono mai riabilitate**, ed è voluto: riguardano il
percorso di memoria del DRS4, mentre il comparatore lavora sul segnale che
arriva dal rivelatore. Applicarle non cambia la decisione del trigger, ma
renderebbe le tracce registrate diverse dal segnale su cui quella decisione è
stata presa. In più sono tarate sull'Output Mode: sui dati in Transparent Mode
lasciano il piedistallo corretto ma gonfiano l'RMS di circa 40 volte
(0.7 conteggi diventano ~28), e quell'RMS è proprio il numero con cui si sceglie
la soglia.

---

## 3. Cose da sapere prima di fidarsi dei numeri

### La latenza sposta l'impulso nella finestra

Il self-trigger ha una latenza di **~320 ns** in modo `paired`, **~420 ns** in
`global`. La posizione dell'impulso nella finestra registrata è:

```
posizione = (1 − PostTriggerSize) × finestra − latenza
```

A 2.5 GS/s con `PostTriggerSize = 10%`: finestra 410 ns, previsione 49 ns,
misurato 50.0 ns. Se la previsione risulta negativa, l'impulso cade **fuori**
dalla finestra e non lo si vede: è quello che succede a 5 GS/s, dove la finestra
dura 205 ns e la latenza è più lunga dell'intera finestra.

### Il comparatore non vede il segnale che vedi tu

In Transparent Mode il DRS4 manda all'ADC una copia attenuata e a banda
limitata del segnale. La conseguenza pratica è che **l'attenuazione dipende
dalla larghezza dell'impulso**:

| larghezza FWHM | mV per unità di offset | attenuazione |
|---|---|---|
| 96 ns | 0.462 | 1.9 |
| 46 ns | 0.475 | 2.0 |
| 7.6 ns | 0.765 | 3.1 |
| 1.6 ns | 3.93 | 16.1 |

Un impulso stretto perde molto più di uno largo, mentre il rumore perde solo il
fattore ~1.9. Tradurre una soglia in millivolt usando la calibrazione sbagliata
porta a sottostimarla di un fattore 8.

I dati stanno in `measurements/larghezza_impulso_20260924.json` e
`measurements/calibrazione_1p6ns_20260925.json`; le figure si rigenerano con
`tools/plot_width_dependence.py` e `tools/plot_diagnostica_1p6ns.py`.

### Il V1742 produce picchi spuri

Picchi larghi 1–3 ns e alti ~12.7 mV, **simultanei su tutti i canali e con la
stessa ampiezza** (rapporto fra canali 1.02 ± 0.04). Difetto noto, segnalato a
CAEN, con patch applicabile solo offline. Si vetano confrontando l'ampiezza fra
canali: una coincidenza vera fra due rivelatori non dà mai rapporto 1.00 ± 4%.
Misura in `measurements/rumore_artefatti_20260925.json`.

### Il rumore in OR si moltiplica per il numero di canali

Il pavimento di rumore misurato è **per canale**. Con N canali in OR il rate va
moltiplicato per N, mentre l'efficienza no. Passare da 1 a 200 canali costa
circa 1.6 conteggi di soglia a parità di fondo, e il rate è dominato dai canali
peggiori, non dalla media: servono soglie per canale e la possibilità di
mascherare i canali rumorosi.

---

## 4. Verificare che funzioni

```bash
# la board è configurata come credi?
#   ConfigureTrigger() chiama DumpSelfTriggerRegisters(), che rilegge
#   0x8000, 0x810C e tutte le 0x1nA8 e le stampa nel log a livello Debug

# la run sta prendendo quello che pensi?
python3 tools/run_check.py --seconds 25
#   ampiezza, larghezza, purezza, rumore, deriva del piedistallo, rate

# dove cade la soglia in millivolt?
python3 tools/noise_scan.py --offsets 4 6 8 10 12 --seconds 30
#   il punto al 50% del plateau, diviso l'ampiezza, dà i mV per offset
```

Lo scan scrive gli estremi in eventi di ogni passo nel suo JSON: servono per
segmentare il file in analisi, e ricostruirli a posteriori da `rate × durata`
non funziona — basta che la DAQ perda qualche evento perché il conto scivoli e
si finisca per analizzare il segmento sbagliato credendo di essere in quello
giusto.
