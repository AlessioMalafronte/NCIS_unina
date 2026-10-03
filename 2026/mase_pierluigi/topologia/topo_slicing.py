#!/usr/bin/env python3
"""Topologia a diamante per topology + service slicing con QoS.

              .-- s2 --.            upper: h1<->h3
    h1,h2 -- s1        s4 -- h3,h4
              `-- s3 --'            lower: h2<->h4

Il ciclo non genera broadcast storm perche' il controller installa regole proattive, 
non usa mai OFPP_FLOOD e risponde direttamente alle ARP. Switch in fail-mode secure.

Uso:  sudo python3 topology/topo_slicing.py [--no-qos]
"""

import argparse
import os
import sys

import yaml
from mininet.cli import CLI
from mininet.link import TCLink #permette di creare link con delay, banda e perdita pacchetti
from mininet.log import setLogLevel, info, error
from mininet.net import Mininet #prende la descr Topo e crea la rete
#OVSK implementazione OpenVswitch, RemoteController serve a mininet per connettersi a controller remoto
from mininet.node import OVSKernelSwitch, RemoteController
from mininet.topo import Topo #descrizione topologia

POLICY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "policy", "slices.yaml")
MBIT = 1_000_000
#ordine di creazione dei link
LINKS = [("s1", "s2"), ("s2", "s4"), ("s1", "s3"), ("s3", "s4")]

#admission control: legge lo yaml e verifica che le richieste di 
#garanzia siano minori della capacità totale del link, ovvero che
#sia possibile soddisfarle.
def load_policy():
    """Carica la policy e verifica che le garanzie stiano nella capacita'."""
    with open(POLICY) as fh:
        #safe_load al posto di load: se trova codice eseguibile (tag python) nello yaml
        # solleva un'eccezione. load invece lo eseguirebbe.
        p = yaml.safe_load(fh)
    #dal dizionario costruito vedo la capacità del link
    cap = p["network"]["link_mbit"]
    #sommo le garanzie richieste da tutti i service slice
    tot = sum(s["guaranteed_mbit"] for s in p["service_slices"])
    # se mi viene richiesta una garanzia maggiore della capacità del link, sollevo un'eccezione
    if tot > cap:                       # admission control
        raise ValueError("Garanzie %d Mbit/s > capacita' %d Mbit/s" % (tot, cap))
    info("*** Policy: %d/%d Mbit/s impegnati sui link\n" % (tot, cap))
    return p


class SlicingTopo(Topo): # Topo è la classe base di mininet, SlicingTopo la estende per definire la topologia.
    """s1 e s4 sono switch di bordo (classificano), s2 e s3 di core."""

    def build(self, policy):
        for s in ("s1", "s2", "s3", "s4"):
            #failmode indica come deve comportarsi lo switch se perde la connessione
            #con il controller.Secure: continua a seguire le regole che il controller
            #aveva installato, tra cui quella che se non è noto un percorso, scarta il pacchetto.
            # standalone: si comporta come uno switch tradizionale, quindi se non
            #conosce il percorso inoltra lo stesso in broadcast. con i loop è un problema.
            self.addSwitch(s, protocols="OpenFlow13", failMode="secure")

        delay = "%dms" % policy["network"]["access_delay_ms"]
        #policy[hosts] è un dizionario, items() restituisce una lista di tuple (nome, dati)
        #sorted ordina per nome. h è il dizionario con ip mac e switch
        for name, h in sorted(policy["hosts"].items()):
            #mininet richiede l'indirizzo con notazione CIDR, per cui /24.
            #aggiungo host e links alla topologia.
            self.addHost(name, ip="%s/24" % h["ip"], mac=h["mac"])
            #TClink vedi import
            self.addLink(name, h["switch"], cls=TCLink, delay=delay)

        # Link inter-switch nudi: l'unico qdisc deve essere quello di OVS,
        # altrimenti si sovrappone a quello di TCLink e la QoS non tiene.
        for a, b in LINKS:
            self.addLink(a, b)


def qos_command(port, policy):
    """Comando ovs-vsctl che crea la gerarchia HTB su una porta."""
    cap = policy["network"]["link_mbit"] * MBIT
    slices = policy["service_slices"]
    qmap = " ".join("queues:%d=@q%d" % (s["queue_id"], s["queue_id"])
                    for s in slices)
    cmd = ["ovs-vsctl",
           "-- set port %s qos=@newqos" % port,
           "-- --id=@newqos create qos type=linux-htb "
           "other-config:max-rate=%d %s" % (cap, qmap)]
    for s in slices:
        opts = "other-config:min-rate=%d" % (s["guaranteed_mbit"] * MBIT)
        if s.get("max_mbit"):
            opts += " other-config:max-rate=%d" % (s["max_mbit"] * MBIT)
        cmd.append("-- --id=@q%d create queue %s" % (s["queue_id"], opts))
    return " ".join(cmd)
    #assembla una stringa, destinata ad una porta specifica.
    #ovs-vsctl -- set port s1-eth3 qos=@newqos
    #      -- --id=@newqos create qos type=linux-htb
    #            other-config:max-rate=10000000
    #            queues:2=@q2 queues:1=@q1 queues:0=@q0
    #          -- --id=@q2 create queue other-config:min-rate=2000000 other-config:max-rate=3000000
    #          -- --id=@q1 create queue other-config:min-rate=5000000
    #          -- --id=@q0 create queue other-config:min-rate=1000000
    #sulla porta s1-eth3 attiva una configurazione QoS di tipo HTB con capacità 10 Mbit/s; 
    #dentro ci sono tre code, la 2 garantita a 2 Mbit/s con tetto 3, la 1 garantita a 5, la 0 garantita a 1.

def configure_queues(net, policy):
    """Code su entrambe le direzioni di ogni link inter-switch."""
    for a, b in LINKS:
        #a e b sono switch, net... restitiuisce la lista di interfacce che collegano a e b. con [0] prendo la prima.
        #intf itera sui due oggetti interfaccia, le porte.
        for intf in net.get(a).connectionsTo(net.get(b))[0]: ## su s1, s2-> [ (<Intf s1-eth3>, <Intf s2-eth1>) ]
            info("*** Code HTB su %s\n" % intf.name)
            #chiamo il qos_command sulla porta.
            os.system(qos_command(intf.name, policy))


def clear_queues():
    """I record QoS/Queue non seguono il ciclo di vita di Mininet."""
    #quando creo le code, sto scrivendo in OVSDB, che non viene cancellato quando mininet termina.
    os.system("ovs-vsctl --all destroy qos > /dev/null 2>&1")
    os.system("ovs-vsctl --all destroy queue > /dev/null 2>&1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-qos", action="store_true",
                    help="scenario baseline: nessuna coda, singola FIFO")
    args = ap.parse_args()

    if os.geteuid() != 0:
        #scrive su stderr
        error("*** Servono privilegi di root (usa sudo)\n")
        sys.exit(1)

    policy = load_policy()
    clear_queues()
    #Crea la descrizione della topologia. specifica il tipo di switch.
    #Controller None perché lo aggiungiamo dopo remoto. link è il tipo predefinito,
    #usato dove non specifico altro tipo. I mac li assegnamo con lo yaml.
    net = Mininet(topo=SlicingTopo(policy=policy), switch=OVSKernelSwitch,
                  controller=None, link=TCLink, autoSetMacs=False)
    #c0 nome interno del controller, remote perché ci si deve connettere.
    #gira nella stessa VM.              
    net.addController("c0", controller=RemoteController,
                      ip="127.0.0.1", port=6653)
    #start fa partire gli switch, il controller e la loro connessione.
    #avvio del processo ovs-vswitchd e ovsdb server.                  
    net.start()

    if args.no_qos:
        info("*** BASELINE: nessuna coda configurata\n")
    else:
        configure_queues(net, policy)

    CLI(net)
    #se si esce dalla CLI, e si interrompe lo script, occorre fare
    #sudo mn -c più un sudo ovs-vsctl --all destroy qos
    #per pulire le code e record creati.
    net.stop()
    clear_queues()


if __name__ == "__main__":
    setLogLevel("info")
    main()
