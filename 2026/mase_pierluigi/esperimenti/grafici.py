#!/usr/bin/env python3
"""
Genera i grafici di confronto tra lo scenario baseline e quello con
service slicing, a partire dai risultati prodotti da esegui.py.

Produce tre figure in risultati/grafici/:
  banda.png     throughput per classe nel tempo, due pannelli affiancati
  ritardo.png   ritardo per classe nel tempo, scala logaritmica
  riepilogo.png ritardo medio e perdita per classe, a barre

Uso:
    python3 esperimenti/grafici.py
    python3 esperimenti/grafici.py --baseline nome1 --qos nome2
"""

import argparse
import csv
import json
import os
import re

import matplotlib
matplotlib.use("Agg")            # nessun display disponibile nella VM
import matplotlib.pyplot as plt

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RISULTATI = os.path.join(BASE, "risultati")

CLASSI = ["realtime", "business", "best_effort"]
COLORI = {"realtime": "#1f77b4", "business": "#2ca02c",
          "best_effort": "#d62728"}


def leggi_dat(percorso):
    """Legge un file .dat di ITGDec: intestazione, poi tempo e valore.

    Le colonne sono: tempo, valore del flusso, aggregato. Con un flusso
    per file le ultime due coincidono, quindi si prende la seconda.
    """
    tempi, valori = [], []
    with open(percorso) as fh:
        next(fh)                                  # salta l'intestazione
        for riga in fh:
            campi = riga.split()
            if len(campi) >= 2:
                tempi.append(float(campi[0]))
                valori.append(float(campi[1]))
    return tempi, valori


def leggi_riepilogo(percorso):
    """Estrae banda, ritardo e perdita dal riepilogo testuale di ITGDec."""
    testo = open(percorso, errors="ignore").read()

    def cerca(schema):
        m = re.search(schema, testo)
        return float(m.group(1)) if m else 0.0

    return {
        "banda": cerca(r"Average bitrate\s+=\s+([\d.]+)"),
        "ritardo": cerca(r"Average delay\s+=\s+([\d.]+)"),
        "perdita": cerca(r"Packets dropped\s+=\s+\d+\s+\(([\d.]+)"),
    }


def serie_temporali(scenari, suffisso, titolo, unita, log=False):
    """Due pannelli affiancati, una curva per classe in ciascuno."""
    fig, assi = plt.subplots(1, 2, figsize=(11, 4), sharey=True)

    for asse, (etichetta, cartella) in zip(assi, scenari.items()):
        for classe in CLASSI:
            percorso = os.path.join(RISULTATI, cartella,
                                    "%s_%s.dat" % (classe, suffisso))
            if not os.path.exists(percorso):
                continue
            t, v = leggi_dat(percorso)
            asse.plot(t, v, label=classe, color=COLORI[classe], linewidth=1.3)

        asse.set_title(etichetta)
        asse.set_xlabel("tempo (s)")
        asse.grid(alpha=0.3)
        if log:
            asse.set_yscale("log")

    assi[0].set_ylabel(unita)
    assi[0].legend(loc="upper right", fontsize=9)
    fig.suptitle(titolo)
    fig.tight_layout()
    return fig


def barre_riepilogo(scenari):
    """Ritardo medio e perdita per classe, nei due scenari."""
    dati = {}
    for etichetta, cartella in scenari.items():
        dati[etichetta] = {}
        for classe in CLASSI:
            percorso = os.path.join(RISULTATI, cartella,
                                    "%s_riepilogo.txt" % classe)
            if os.path.exists(percorso):
                dati[etichetta][classe] = leggi_riepilogo(percorso)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    larghezza = 0.35
    x = range(len(CLASSI))

    for i, (etichetta, valori) in enumerate(dati.items()):
        pos = [p + i * larghezza for p in x]
        a1.bar(pos, [valori.get(c, {}).get("ritardo", 0) * 1000 for c in CLASSI],
               larghezza, label=etichetta)
        a2.bar(pos, [valori.get(c, {}).get("perdita", 0) for c in CLASSI],
               larghezza, label=etichetta)

    for asse, titolo, unita in ((a1, "Ritardo medio", "ms"),
                                (a2, "Perdita", "%")):
        asse.set_xticks([p + larghezza / 2 for p in x])
        asse.set_xticklabels(CLASSI)
        asse.set_title(titolo)
        asse.set_ylabel(unita)
        asse.grid(axis="y", alpha=0.3)
        asse.legend(fontsize=9)

    # Il ritardo varia di tre ordini di grandezza: senza scala
    # logaritmica i valori bassi risulterebbero invisibili.
    a1.set_yscale("log")
    fig.tight_layout()
    return fig


def code_monitor(scenari, porta=3):
    """Throughput per coda misurato dal controller, non dal ricevitore.

    E' il punto di vista dello switch: mostra come il traffico si
    distribuisce fra le tre code sulla porta congestionata. I dati
    vengono dal modulo di monitoraggio del controller, che interroga
    periodicamente le queue stats via OpenFlow.
    """
    etichette = {0: "coda 0 (best effort)", 1: "coda 1 (business)",
                 2: "coda 2 (realtime)"}
    colori = {0: COLORI["best_effort"], 1: COLORI["business"],
              2: COLORI["realtime"]}

    fig, assi = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for asse, (etichetta, cartella) in zip(assi, scenari.items()):
        serie = leggi_queue_stats(cartella, porta)
        for coda in sorted(serie):
            t, v = serie[coda]
            asse.plot(t, v, label=etichette.get(coda, "coda %d" % coda),
                      color=colori.get(coda), marker="o", markersize=3,
                      linewidth=1.3)
        asse.set_title(etichetta)
        asse.set_xlabel("tempo (s)")
        asse.grid(alpha=0.3)

    assi[0].set_ylabel("Mbit/s")
    assi[0].legend(loc="upper right", fontsize=9)
    fig.suptitle("Occupazione delle code sulla porta di s1 verso s2 "
                 "(misure raccolte dal controller)")
    fig.tight_layout()
    return fig


def leggi_queue_stats(cartella, porta):
    """Legge il CSV del monitor, limitandosi a una porta di uscita.

    Il CSV copre l'intera sessione del controller, non solo
    l'esperimento: la finestra utile e' quella registrata in meta.json,
    e i tempi vengono riportati a zero sull'istante di inizio.
    """
    percorso = os.path.join(RISULTATI, cartella, "queue_stats.csv")
    meta = json.load(open(os.path.join(RISULTATI, cartella, "meta.json")))
    t0, t1 = meta["t_inizio"], meta["t_fine"]

    serie = {}
    with open(percorso) as fh:
        for riga in csv.DictReader(fh):
            if riga["switch"] != "s1" or int(riga["port"]) != porta:
                continue
            t = float(riga["t"])
            if not t0 <= t <= t1:
                continue
            coda = int(riga["queue"])
            serie.setdefault(coda, ([], []))
            serie[coda][0].append(t - t0)
            serie[coda][1].append(float(riga["mbit_s"]))
    return serie


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="slicing_baseline_finale")
    ap.add_argument("--qos", default="slicing_qos_finale")
    args = ap.parse_args()

    scenari = {"Baseline (coda unica)": args.baseline,
               "Service slicing + QoS": args.qos}

    uscita = os.path.join(RISULTATI, "grafici")
    os.makedirs(uscita, exist_ok=True)

    figure = [
        ("banda.png", serie_temporali(
            scenari, "banda", "Throughput per classe", "Kbit/s")),
        ("ritardo.png", serie_temporali(
            scenari, "ritardo", "Ritardo per classe", "secondi", log=True)),
        ("riepilogo.png", barre_riepilogo(scenari)),
        ("code.png", code_monitor(scenari)),
    ]

    for nome, fig in figure:
        percorso = os.path.join(uscita, nome)
        fig.savefig(percorso, dpi=150)
        print("scritto %s" % percorso)


if __name__ == "__main__":
    main()
