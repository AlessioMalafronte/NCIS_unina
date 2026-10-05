#!/usr/bin/env python3
"""
Esegue uno scenario sperimentale completo e ne raccoglie i risultati.
 
Costruisce la stessa topologia di topologia/topo_slicing.py, genera i tre
flussi definiti nella policy con D-ITG, e salva i log in risultati/<nome>/.
 
Il controller deve essere gia' in ascolto, e il suo scenario deve essere
coerente con quello di questo script:
 
    ryu-manager controller/slicing_controller.py
    sudo python3 esperimenti/esegui.py --nome slicing
 
    SLICING_BASELINE=1 ryu-manager controller/slicing_controller.py
    sudo python3 esperimenti/esegui.py --nome baseline --no-qos
"""
 
import argparse
import glob
import json
import os
import shutil
import sys
import time
 
from mininet.log import setLogLevel, info, error
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController
from mininet.link import TCLink
 
# La topologia e le sue funzioni di supporto stanno nell'altro modulo:
# vengono riusate invece di essere duplicate.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "topologia"))
from topo_slicing import (SlicingTopo, load_policy, configure_queues,
                          clear_queues)
 
RISULTATI = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "risultati")
 
 
def avvia_ricevitori(host, policy, cartella):
    """Un ITGRecv per flusso, ciascuno su una porta di segnalazione propria.
 
    Con un solo ricevitore condiviso l'associazione tra flusso e file di
    log (indicata dai mittenti con -x) non e' deterministica: se le
    segnalazioni arrivano sovrapposte, due flussi finiscono nello stesso
    registro. Separando i canali di segnalazione il problema non si pone.
    """
    processi = []
    for i, f in enumerate(policy["experiment"]["flows"]):
        porta = 9000 + i
        cmd = ("ITGRecv -Sp %d -l %s/%s_ricevitore.log"
               % (porta, cartella, f["name"]))
        info("*** ITGRecv su %s per %s (segnalazione %d)\n"
             % (host.name, f["name"], porta))
        processi.append(host.popen(cmd))
    return processi
 
 
def avvia_flussi(host, policy, cartella):
    """Un ITGSend per flusso, tutti in parallelo.
 
    Il tasso si esprime in pacchetti al secondo: D-ITG non accetta una
    banda, quindi si converte dividendo per la dimensione del pacchetto.
    """
    exp = policy["experiment"]
    dst_ip = policy["hosts"][exp["dest"]]["ip"]
    durata_ms = exp["duration_s"] * 1000
    processi = []
 
    for i, f in enumerate(exp["flows"]):
        pps = int(f["rate_mbit"] * 1e6 / (f["packet_bytes"] * 8))
        cmd = ("ITGSend -a %s -Sdp %d -rp %d -T %s -C %d -c %d -t %d "
               "-l %s/%s_mittente.log"
               % (dst_ip, 9000 + i, f["dst_port"], f["proto"], pps,
                  f["packet_bytes"], durata_ms, cartella, f["name"]))
        info("*** flusso %-12s %s:%d  %d pps\n"
             % (f["name"], f["proto"], f["dst_port"], pps))
        processi.append(host.popen(cmd))
 
    return processi
 
 
def avvia_incrociato(net, policy, cartella):
    """Carico sulla slice inferiore, per provare l'isolamento tra tenant.
 
    h2->h4 attraversa s3, un percorso che non condivide alcun link con
    quello di h1->h3. Se il topology slicing isola davvero i tenant, le
    metriche della slice superiore non devono cambiare rispetto
    all'esecuzione senza questo carico.
    """
    c = policy["experiment"].get("cross_traffic")
    if not c:
        return []
 
    src, dst = net.get(c["source"]), net.get(c["dest"])
    dst_ip = policy["hosts"][c["dest"]]["ip"]
    pps = int(c["rate_mbit"] * 1e6 / (c["packet_bytes"] * 8))
    durata_ms = policy["experiment"]["duration_s"] * 1000
    porta_seg = 9010                      # fuori dall'intervallo dei flussi
 
    recv = dst.popen("ITGRecv -Sp %d -l %s/incrociato_ricevitore.log"
                     % (porta_seg, cartella))
    time.sleep(1)
    send = src.popen("ITGSend -a %s -Sdp %d -rp %d -T %s -C %d -c %d -t %d "
                     "-l %s/incrociato_mittente.log"
                     % (dst_ip, porta_seg, c["dst_port"], c["proto"], pps,
                        c["packet_bytes"], durata_ms, cartella))
    info("*** carico incrociato %s->%s  %s:%d  %d pps\n"
         % (c["source"], c["dest"], c["proto"], c["dst_port"], pps))
    return [recv, send]
 
 
def decodifica(cartella):
    """Trasforma i log binari di D-ITG in testo e in serie temporali.
 
    Scandisce i log presenti invece di seguire la lista dei flussi, cosi'
    include anche il carico incrociato quando c'e'.
    """
    for log in sorted(glob.glob(os.path.join(cartella, "*_ricevitore.log"))):
        base = log[:-len("_ricevitore.log")]
        os.system("ITGDec %s > %s_riepilogo.txt 2>&1" % (log, base))
        os.system("ITGDec %s -b 1000 %s_banda.dat > /dev/null 2>&1" % (log, base))
        os.system("ITGDec %s -d 1000 %s_ritardo.dat > /dev/null 2>&1" % (log, base))
        os.system("ITGDec %s -j 1000 %s_jitter.dat > /dev/null 2>&1" % (log, base))
        os.system("ITGDec %s -p 1000 %s_perdita.dat > /dev/null 2>&1" % (log, base))
        info("*** decodificato %s\n" % os.path.basename(base))
def salva_contatori(net, cartella):
    """Fotografa le classi HTB prima che la rete venga smontata.

    I contatori sono cumulativi e vivono nel kernel: dopo net.stop()
    non esistono piu'. lended e borrowed sono la prova diretta del
    comportamento work-conserving dello scheduler.
    """
    percorso = os.path.join(cartella, "tc_class.txt")
    with open(percorso, "w") as fh:
        for intf in ("s1-eth3", "s1-eth4"):
            fh.write("== %s\n" % intf)
            fh.write(os.popen("tc -s class show dev %s" % intf).read())
            fh.write("\n")
    info("*** contatori HTB in %s\n" % percorso)
 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nome", required=True,
                    help="nome dello scenario (sottocartella dei risultati)")
    ap.add_argument("--no-qos", action="store_true",
                    help="scenario baseline: nessuna coda configurata")
    ap.add_argument("--incrociato", action="store_true",
                    help="satura anche la slice inferiore (h2->h4)")
    args = ap.parse_args()
 
    if os.geteuid() != 0:
        error("*** Servono privilegi di root (usa sudo)\n")
        sys.exit(1)
 
    policy = load_policy()
    exp = policy["experiment"]
 
    cartella = os.path.abspath(os.path.join(RISULTATI, args.nome))
    shutil.rmtree(cartella, ignore_errors=True)
    os.makedirs(cartella)
 
    clear_queues()
    net = Mininet(topo=SlicingTopo(policy=policy), switch=OVSKernelSwitch,
                  controller=None, link=TCLink, autoSetMacs=False)
    net.addController("c0", controller=RemoteController,
                      ip="127.0.0.1", port=6653)
    net.start()
 
    if args.no_qos:
        info("*** BASELINE: nessuna coda configurata\n")
    else:
        configure_queues(net, policy)
 
    # Il controller installa le regole alla connessione dello switch:
    # senza questa pausa i primi pacchetti troverebbero tabelle incomplete.
    info("*** attesa installazione regole\n")
    time.sleep(5)
 
    src, dst = net.get(exp["source"]), net.get(exp["dest"])
 
    # Verifica preliminare: se il percorso non e' attivo, inutile misurare.
    if src.cmd("ping -c 2 -W 2 %s" % dst.IP()).find("2 received") < 0:
        error("*** %s non raggiunge %s: controller attivo?\n"
              % (src.name, dst.name))
        net.stop()
        clear_queues()
        sys.exit(1)
 
    ricevitori = avvia_ricevitori(dst, policy, cartella)
    time.sleep(2)
 
    incrociato = avvia_incrociato(net, policy, cartella) if args.incrociato else []
 
    t_inizio = time.time()
    processi = avvia_flussi(src, policy, cartella)
 
    info("*** generazione in corso per %d s\n" % exp["duration_s"])
    for p in processi:
        p.wait()
    t_fine = time.time()
 
    for r in ricevitori + incrociato:
        r.terminate()
    time.sleep(1)
 
    decodifica(cartella)
    salva_contatori(net, cartella)
    # Istantanea delle statistiche per coda raccolte dal controller, piu'
    # la finestra temporale che permette di isolare questa esecuzione.
    csv = os.path.join(os.path.dirname(RISULTATI), policy["monitor"]["csv"])
    if os.path.exists(csv):
        shutil.copy(csv, os.path.join(cartella, "queue_stats.csv"))
 
    with open(os.path.join(cartella, "meta.json"), "w") as fh:
        json.dump({"scenario": args.nome, "qos": not args.no_qos,
                   "incrociato": args.incrociato,
                   "t_inizio": t_inizio, "t_fine": t_fine,
                   "durata_s": exp["duration_s"]}, fh, indent=2)
 
    info("*** risultati in %s\n" % cartella)
 
    # Lo script gira come root: senza questo i risultati non sarebbero
    # modificabili dall'utente che ha lanciato sudo.
    utente = os.environ.get("SUDO_USER")
    if utente:
        os.system("chown -R %s:%s %s" % (utente, utente, cartella))
 
    net.stop()
    clear_queues()
 
 
if __name__ == "__main__":
    setLogLevel("info")
    main()
 
