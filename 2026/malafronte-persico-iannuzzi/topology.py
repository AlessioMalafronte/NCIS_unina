#!/usr/bin/env python3
"""
topology.py - Topologia per il project work "Network Slicing" (NCI 2025/26)

            h1 ---.                            .--- h3
                   \                          /
                    [S1] === S2 (10 Mbps) === [S4]        <-- slice UPPER
                     |  \                    /  |
            h2 ---'   `=== S3 (1 Mbps) ====='   `--- h4   <-- slice LOWER

Uso:
    sudo python3 topology.py            # topologia normale
    sudo python3 topology.py --stp      # con STP (solo per test preliminari)
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info
import sys

# --- Parametri dei link ------------------------------------------------------
# I link host-switch sono volutamente larghi: il collo di bottiglia deve
# essere il percorso core, altrimenti lo slicing non e' osservabile.
HOST_BW = 100          # Mbps
HOST_DELAY = '0.1ms'

UPPER_BW = 10          # Mbps - percorso via S2
UPPER_DELAY = '2ms'

LOWER_BW = 1           # Mbps - percorso via S3
LOWER_DELAY = '5ms'    # ritardo diverso -> secondo modo per identificare lo slice


def build_network(enable_stp=False):
    net = Mininet(controller=RemoteController,
                  switch=OVSKernelSwitch,
                  link=TCLink,
                  autoSetMacs=False,          # i MAC li fissiamo noi, servono al controller
                  build=False)

    info('*** Aggiungo il controller remoto (Ryu)\n')
    net.addController('c0', controller=RemoteController,
                      ip='127.0.0.1', port=6653)

    info('*** Aggiungo gli host (MAC statici: il controller slicing ragiona sui MAC)\n')
    h1 = net.addHost('h1', mac='00:00:00:00:00:01', ip='10.0.0.1/24')
    h2 = net.addHost('h2', mac='00:00:00:00:00:02', ip='10.0.0.2/24')
    h3 = net.addHost('h3', mac='00:00:00:00:00:03', ip='10.0.0.3/24')
    h4 = net.addHost('h4', mac='00:00:00:00:00:04', ip='10.0.0.4/24')

    info('*** Aggiungo gli switch (OpenFlow 1.3)\n')
    s1 = net.addSwitch('s1', protocols='OpenFlow13')   # edge lato h1/h2
    s2 = net.addSwitch('s2', protocols='OpenFlow13')   # core percorso UPPER
    s3 = net.addSwitch('s3', protocols='OpenFlow13')   # core percorso LOWER
    s4 = net.addSwitch('s4', protocols='OpenFlow13')   # edge lato h3/h4

    # --- Link host-switch ---------------------------------------------------
    # I numeri di porta sono FISSATI esplicitamente: il controller li usa come
    # costanti, quindi non devono dipendere dall'ordine di creazione.
    info('*** Link host <-> switch di accesso\n')
    net.addLink(h1, s1, port1=0, port2=1, delay=HOST_DELAY)
    net.addLink(h2, s1, port1=0, port2=2, delay=HOST_DELAY)
    net.addLink(h3, s4, port1=0, port2=1, delay=HOST_DELAY)
    net.addLink(h4, s4, port1=0, port2=2, delay=HOST_DELAY)

    # --- Link core: percorso UPPER (S1 - S2 - S4), 10 Mbps ------------------
    info('*** Link core UPPER (via s2, %d Mbps)\n' % UPPER_BW)
    net.addLink(s1, s2, port1=3, port2=1, bw=UPPER_BW, delay=UPPER_DELAY)
    net.addLink(s2, s4, port1=2, port2=3, bw=UPPER_BW, delay=UPPER_DELAY)
    net.addLink(s1, s2, port1=3, port2=1, bw=UPPER_BW, delay=UPPER_DELAY, max_queue_size=100)
    net.addLink(s2, s4, port1=2, port2=3, bw=UPPER_BW, delay=UPPER_DELAY, max_queue_size=100)

    # --- Link core: percorso LOWER (S1 - S3 - S4), 1 Mbps -------------------
    info('*** Link core LOWER (via s3, %d Mbps)\n' % LOWER_BW)
    net.addLink(s1, s3, port1=4, port2=1, bw=LOWER_BW, delay=LOWER_DELAY)
    net.addLink(s3, s4, port1=2, port2=4, bw=LOWER_BW, delay=LOWER_DELAY)
    net.addLink(s1, s3, port1=4, port2=1, bw=LOWER_BW, delay=LOWER_DELAY, max_queue_size=100)
    net.addLink(s3, s4, port1=2, port2=4, bw=LOWER_BW, delay=LOWER_DELAY, max_queue_size=100)

    info('*** Costruisco e avvio la rete\n')
    net.build()
    net.start()

        # Ubuntu genera traffico multicast IPv6 (MLD, router solicitation) su ogni
    # interfaccia: MAC sorgente sconosciuti al controller, che intasano il log
    # senza appartenere ad alcuno slice.
    info('*** Disattivo IPv6 su host e switch\n')
    for host in net.hosts:
        host.cmd('sysctl -w net.ipv6.conf.all.disable_ipv6=1')
        host.cmd('sysctl -w net.ipv6.conf.default.disable_ipv6=1')
    for sw in net.switches:
        for intf in sw.intfList():
            if intf.name != 'lo':
                sw.cmd('sysctl -w net.ipv6.conf.%s.disable_ipv6=1' % intf.name)

    if enable_stp:
        # STP serve SOLO per i test preliminari con simple_switch_13:
        # la topologia contiene un anello (s1-s2-s4-s3-s1) e uno switch
        # ad apprendimento che fa flooding genera un broadcast storm.
        # Con il controller di slicing lo STP va DISATTIVATO.
        info('*** Abilito STP sugli switch (modalita\' diagnostica)\n')
        for sw in (s1, s2, s3, s4):
            sw.cmd('ovs-vsctl set bridge %s stp_enable=true' % sw.name)
        info('*** Attendo la convergenza dello STP (~30 s)...\n')
        import time
        time.sleep(35)

    print_port_map()
    return net


def print_port_map():
    """Promemoria delle porte: sono le costanti usate dal controller."""
    info('\n=== MAPPA DELLE PORTE ===\n')
    info('  s1: 1->h1   2->h2   3->s2 (UPPER)   4->s3 (LOWER)\n')
    info('  s2: 1->s1   2->s4                     [core UPPER]\n')
    info('  s3: 1->s1   2->s4                     [core LOWER]\n')
    info('  s4: 1->h3   2->h4   3->s2 (UPPER)   4->s3 (LOWER)\n')
    info('=========================\n\n')


if __name__ == '__main__':
    setLogLevel('info')
    stp = '--stp' in sys.argv
    net = build_network(enable_stp=stp)
    CLI(net)
    net.stop()