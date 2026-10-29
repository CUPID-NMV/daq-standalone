# analisi/

Script di analisi e di diagnostica scritti per una misura precisa, conservati
perche' i grafici si possano rifare. Gli strumenti generali stanno in
`tools/` (`carica_padme.py`, `padme.py`, `daqio.py`, ...); qui c'e' cio' che
li usa per rispondere a una domanda.

Tutti gli script Python trovano da soli la radice del repository (`ROOT`) e
importano da `tools/`: si lanciano da qualunque cartella, sul PC DAQ o sul
Mac, purche' abbiano i dati. I file di dati si passano come argomento; i
risultati intermedi (`.npz`, `.npy`, `.json`) stanno in `measurements/`. I PNG
non vanno su git (`*.png` e' in `.gitignore`): si rifanno dagli script.

Sul Mac servono `numpy`, `h5py`, `matplotlib`; i `.c` invece si compilano e
girano solo sul PC DAQ, che ha la libreria CAEN:
`gcc -O2 -o majthr majthr.c -lCAENVME`.

I JSON delle misure registrano il file di dati col percorso del PC DAQ
(`/home/daq/daq-standalone/data/...`): sul Mac va passato quello locale.

---

## 20261028_carica_pmt_sipm/ -- carica dei PMT e dei SiPM, LNGS e Frascati

Domanda: com'e' fatta la carica dei PMT al Gran Sasso, dove stanno i muoni,
e perche' i SiPM con un cancello lungo hanno code negative. Run usate:

| run | dove | trigger | cosa |
|---|---|---|---|
| `LNGS__54-0V_0011_750Ms_55PT` | LNGS | self-trigger OR su ch20/21 (PMT) | 5337 ev., 3.3 h |
| `LNGS__51-0V_0000_750Ms_55PT` | LNGS | idem, SiPM a 51 V (sotto breakdown) | 128 ev. |
| `LNGS__54-0V_0000_750Ms_55PT` | LNGS | V812, 4 canali su 5 | 38 ev. in 23.4 h |
| `test_54-0V_0005_750Ms_60PT` | Frascati | V812 (in realta' 2 su 3, vedi CLAUDE.md) | 77382 ev., 13.6 h, senza SIC |

Prima le cariche per evento, con `tools/carica_padme.py` (dalla radice):

```
R=data/LNGS/LNGS__54-0V_0011_750Ms_55PT.h5.gz
python3 tools/carica_padme.py $R --ped 0 150 --gate 240 500 --out measurements/carica_0011_testbeam
python3 tools/carica_padme.py $R --ped 0 150 --gate 240 500 --padme piedistallo --out measurements/carica_0011_piedistallo
python3 tools/carica_padme.py $R --canali 16 17 18 --ped 0 150 --gate 200 1350 --out measurements/carica_0011_sipm_g200-fine
python3 tools/carica_padme.py $R --canali 16 17 18 --ped 0 150 --gate 240 500 --out measurements/carica_0011_sipm_g240-500
F=data/test_54-0V_0005_750Ms_60PT.h5.gz
python3 tools/carica_padme.py $F --canali 24 26 --padme nessuna --out measurements/carica_frascati_0005_auto
python3 tools/carica_padme.py $F --canali 24 26 --padme nessuna --ped 0 150 --gate 150 600 --out measurements/carica_frascati_0005_p0-150_g150-600
```

Poi gli script, nell'ordine in cui sono stati fatti. `F` = file di dati,
`OUT` = PNG (o prefisso) da scrivere.

| script | argomenti | cosa mostra |
|---|---|---|
| `finestre_diag.py` | F(0011) OUT | SiPM: frazione dell'impulso nel cancello; oscillazione nei primi 100 ns |
| `wf_ped.py` | F(0011) CANALE OUT | 20 forme d'onda di solo piedistallo di un PMT, con e senza PADME (ricetta del test beam) |
| `padme_confronto.py` | F(0011) OUT | PADME del test beam (`wclib.py`) contro quella sulla finestra del piedistallo |
| `confronto_ricette.py` | NPZ_testbeam NPZ_piedistallo OUT | carica con le due ricette e senza PADME, differenza evento per evento |
| `sipm_gate.py` | F(0011) OUT | SiPM: cancello 200-fine contro 240-500 |
| `blank_vs_L.py` | F(0011) OUT | rumore sulla carica contro la lunghezza della finestra (cresce linearmente) |
| `raw_charge.py` | F(0011) OUT | SiPM: integrale senza sottrazione del piedistallo |
| `dove_vanno.py` | F(0011) OUT | dove finiscono senza sottrazione gli eventi con carica negativa |
| `sipm_51_54.py` | F(51 V) F(0011) PREFISSO | SiPM a 51 V contro 54 V: piedistallo senza dark count (`_A`, `_B`, `_C`) |
| `raw_bassi.py` | F(51 V) F(0011) OUT | integrale grezzo assoluto e forme d'onda degli eventi piu' bassi |
| `fra_lngs.py` | OUT | PMT Frascati contro LNGS, non normalizzati |
| `fra_taglio_sipm.py` | F(Frascati) OUT | taglio "3 SiPM > 500000 ADC·ns grezzi"; scrive `frascati_0005_sel_3sipm_500k.npy` |
| `fra_mezzo.py` | F(Frascati) OUT | eventi dei SiPM fra piedistallo e saturazione: tempo, pannelli, ampiezza, WC |
| `fra_lngs_sel.py` | OUT | PMT Frascati con il taglio sui SiPM contro LNGS |
| `lngs24.py` | F(LNGS 0000) OUT | i 38 eventi della run da 24 h; scrive `lngs_0000_eventi.npz` |
| `muoni_overlay.py` | OUT | dove cadono i due muoni LNGS (eventi 17 e 24) |
| `lngs_3sigma.py` | OUT | LNGS 0011 con entrambi i PMT > 3σ del piedistallo |
| `lngs_3sigma_norm.py` | OUT | idem, normalizzati in area |
| `lngs24_pe.py` | F(LNGS 0000) OUT | i SiPM dei 38 eventi in fotoelettroni: nessun evento con due dark count |
| `muoni_con_wf.py` | F(LNGS 0000) OUT | spettri normalizzati con sotto le forme d'onda dei PMT dei due muoni |
| `muoni_con_wf_sipm.py` | F(LNGS 0000) OUT | idem con anche i SiPM, PMT in polarita' vera: e' la figura da mostrare |

## v812_maggioranza/ -- calibrazione della somma del V812 (27 e 28/10)

**`scan_c.py` e `scan_c2.py` comandano la DAQ**: avviano e fermano run dal
controllore (porta 8766), scrivono il registro 0x48 del V812 con `majthr` e
riscrivono `run-local.toml`. Non lanciarli durante una presa dati. Scrivono i
confini dei passi in `/tmp/scan_c*.json`; le versioni conservate sono
`measurements/v812_majthr_2026102{7,8}.json`.

| file | cosa |
|---|---|
| `majthr.c` | scrive un valore grezzo nel registro della soglia di maggioranza (0xDDDD0048) |
| `scan_c.py`, `scan_c2.py` | scan della soglia a run aperta (27/10 con S aperta, 28/10 con S terminata) |
| `analisi_c.py`, `analisi_c2.py` | molteplicita' dei SiPM per passo, dai JSON in `measurements/` |
| `plot_c.py` | grafico dello scan del 27/10 (il confronto dei due e' `tools/plot_majthr.py`) |
| `provamaj.py` | prova di 30 s per livello di maggioranza (la verifica "Majority = 20") |
| `allin.py` | molteplicita' dei SiPM sulle run di scan del 27/10 |

## v1742_clock/ -- diagnostica del clock della scheda 202 (28/10)

| file | cosa |
|---|---|
| `leggireg.c` | legge 0x8104, 0x8178, 0x8100, ... sulle due schede (base 0x32100000 e 0x11110000) |
| `riarma.c` | riarma il flag di sgancio del PLL (lettura di 0xEF04) e rilegge 0x8104 |

## 20261026_raffiche_dcr/ -- raffiche col trigger software e DCR (26/10)

| file | cosa |
|---|---|
| `raffiche.py`, `raffiche_nomask.py` | cerca le raffiche a ~176 MHz agganciate al trigger software sui PMT |
| `confronto_dcr.py` | confronto delle stime di DCR (run 0036 e 0039, tratto pulito e finestra intera) |
| `toml_dcr.py` | **riscrive `run-local.toml`** per la run di DCR a trigger software |

## 20261029_compton_mc/ -- gamma di Hall A simulati contro i dati LNGS

| script | argomenti | cosa mostra |
|---|---|---|
| `gate_frascati.py` | F(Frascati) F(0011) OUT | muoni di Frascati col cancello della 0011 allineato sull'impulso (+78 ns) |
| `fondo_mc_somma.py` | MC_DIR OUT | fondo MC = Compton + muoni di Hall A, normalizzato al tempo, sopra lo spettro LNGS, con il rapporto dati/MC |
| `normalizzazione_tempo.py` | MC_DIR OUT | dati 0011, Compton e muoni di Hall A simulati normalizzati allo stesso tempo di acquisizione |
| `sovrapposizione_tempo.py` | MC_DIR OUT | come `sovrapposizione.py`, con il Compton normalizzato al tempo della 0011 e LNF riscalato sulla coda LNGS |
| `sovrapposizione_area_tempo.py` | MC_DIR OUT | come `sovrapposizione.py` (dati normalizzati in area), con il Compton normalizzato al tempo della 0011 in unita' di eventi LNGS |
| `sovrapposizione.py` | MC_DIR OUT | LNF, LNGS, candidati muoni e Compton simulato, normalizzati in area |
| `verifica_mc_gamma.py` | MC_DIR OUT | verifica del MC dei gamma: spettro generato contro la macro, soglia Cherenkov, processi che fanno luce |
| `compton_vs_dati.py` | MC_DIR OUT | spettro Compton dei gamma di Hall A visto dal WC prototipo (Geant4 di `Testbeam2026_WC_unified`, geometria `single`) e muoni LNGS simulati, in rate assoluto, sopra la carica dei PMT della 0011 |

`MC_DIR` e' la cartella `data/` di `~/Testbeam2026_WC_unified`; serve `uproot`
(sul Mac c'e' in `~/venvs/jupyter-global`). La scala ADC·ns per fotoelettrone
non e' misurata: lo script la ricava dai muoni (Frascati e LNGS) e mostra le
due stime.

## 20261030_digit_mc/ -- MC digitalizzato come i dati LNGS

| script | argomenti | cosa fa |
|---|---|---|
| `digit_mc.py` | MC_DIR NOME OUT.npz [REPLICHE] [--k] [--rate] | fotoni del MC -> QE del PMT H14600-103 (datasheet TPMO1097E05) -> PE con TTS e risoluzione -> forma dell'impulso misurata -> somma a tracce vere di piedistallo della 0013 -> stessa analisi e stesso trigger dei dati |
| `calibra_k.py` | MUONI_DIGIT.npz | scala ADC·ns per PE (PMT x scheda di amplificazione) dai muoni MC contro i muoni LNF; scrive `measurements/mc_digit_K.json` |
| `confronto_0013.py` | OUT | fondo LNGS 0013 contro Compton (righe e NaI) e muoni MC digitalizzati, in rate |

Perche' non `Signal_maker.ipynb`: rappresenta la catena del test beam (PMT senza
amplificatore, PE da ~3 mV su 3.5 conteggi di rumore bianco); portato alla scala
LNGS il rumore integrato nel cancello valeva ~6 PE.
Richiede `measurements/libreria_digit_0013.npz` (da `20261030_fondo_0013/libreria_digit.py`).

## 20261029_scint_fibre/ -- l'eccesso LNGS e la scintillazione delle fibre

| script | argomenti | cosa mostra |
|---|---|---|
| `forma_coda.py` | F(0013) F(Frascati 0005) OUT | forma degli impulsi a parita' d'ampiezza (FWHM, frazione di carica dopo +20 e +40 ns): coincidenze LNGS, impulsi di un solo PMT, muoni LNF, e le previsioni per luce pronta e per scintillazione del polistirene delle fibre |
| `mc_scint_vs_dati.py` | MC_SCINT.npz OUT [ETICHETTA] | fondo LNGS 0013 contro il MC digitalizzato senza e con la scintillazione del nucleo delle fibre (in rate, per classe) |
| `macro_sorgente_pmt.py` | PROGETTO_MC | scrive `gun_gammas_pmt_{K40,U238,Th232}.mac` (gamma dal volume dei moduli dei PMT) e `measurements/sorgente_pmt_linee.json` |
| `sorgente_pmt.py` | MC_DIR TAG_K40 TAG_U238 TAG_TH232 HALLA.npz OUT | attivita' nei moduli dei PMT necessaria per l'eccesso, e forma/asimmetria che ne verrebbero |

Niente tagli in carica sull'impulso che si misura: a parita' d'ampiezza sceglierebbero
la forma (la prima versione lo faceva).

## 20261029_normalizzazione_gamma/ -- quanto vale il flusso dei gamma di Hall A

| script | argomenti | cosa mostra |
|---|---|---|
| `flusso_haffke.py` | HAFFKE.pdf OUT | legge le curve della Fig. 2 di arXiv:1101.5298 dal PDF vettoriale e le integra: il "flusso" della Tab. 1 sono i gamma entranti per cm^2 di superficie del NaI (J = phi/4). Scrive `measurements/flusso_haffke.json` |
| `confronto_rinormalizzato.py` | OUT [PRIMA.npz] | fondo LNGS 0013 contro i gamma di Hall A digitalizzati con la normalizzazione corretta, per classe di carica e come rapporto dati/MC; PRIMA.npz disegna anche una digitalizzazione vecchia |
| `soglie_lngs.py` | F1.h5.gz [F2 ...] | per run: bordo del self-trigger nell'ampiezza registrata (2 percentile degli eventi con un PMT solo), sigma della carica a vuoto, t50, durata dal `.log`; scrive `measurements/soglie_trigger_lngs.json`. Gira sul PC DAQ, dove sono i dati |
| `sovrapposizione_0011_0013.py` | OUT | fondo LNGS 0011 + 0013 sommato contro MC gamma + muoni, col trigger e il taglio di ogni run pesati con la durata; asse logaritmico fino a 200000 ADC·ns, scala in PE |

Il PDF non sta nel repository: si scarica da arXiv. `flusso_haffke.py` richiede
`pymupdf` (sul Mac c'e' in `~/venvs/jupyter-global`).
Dal 2026-10-29 i gamma si generano con J * A = 0.28 x 49400 cm^2 = 13832 Hz
(`digit_mc.py` e gli script di `20261029_compton_mc/` e `20261030_fondo_0013/`);
prima con 0.35/4 x A = 4322 Hz, 3.2 volte meno. I `.npz` di `measurements/`
col MC digitalizzato sono rifatti cosi', con K = 1696 dai muoni con la
scintillazione delle fibre.
