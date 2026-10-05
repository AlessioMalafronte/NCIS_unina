#!/usr/bin/env python3
import json
import os
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch, Node
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

class LinuxRouter(Node):
    def config(self, **params):
        super(LinuxRouter, self).config(**params)
        self.cmd('sysctl net.ipv4.ip_forward=1')

    def terminate(self):
        self.cmd('sysctl net.ipv4.ip_forward=0')
        super(LinuxRouter, self).terminate()

def run_topo_noqos():
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch, link=TCLink)

    info('Adding Controller\n')
    c0 = net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)

    info('Adding Router\n')
    r1 = net.addHost('r1', cls=LinuxRouter, ip='10.0.1.254/24', mac='00:00:00:00:0a:01')
    
    info('Adding Switches\n')
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    info('Adding Hosts\n')
    h1 = net.addHost('h1', ip='10.0.1.1/24', mac='00:00:00:00:01:01', defaultRoute='via 10.0.1.254')
    m1 = net.addHost('m1', ip='10.0.1.2/24', mac='00:00:00:00:01:02', defaultRoute='via 10.0.1.254')
    h2 = net.addHost('h2', ip='10.0.2.1/24', mac='00:00:00:00:02:01', defaultRoute='via 10.0.2.254')
    h3 = net.addHost('h3', ip='10.0.2.2/24', mac='00:00:00:00:02:02', defaultRoute='via 10.0.2.254')

    # Salva i PID degli host per permettere ad altri script di agganciarsi
    with open('/tmp/mininet_pids.env', 'w') as f:
        for host in net.hosts:
            f.write(f"{host.name}={host.pid}\n")

    info('Creating Links\n')
    net.addLink(h1, s1, port1=0, port2=1)
    net.addLink(m1, s1, port1=0, port2=2)

    # BOTTLENECK: Link a 10 Mbps con buffer FIFO standard di 10 pacchetti (senza classi HTB)
    net.addLink(s1, r1, port1=3, port2=0, bw=10, max_queue_size=10, params2={'ip': '10.0.1.254/24'})

    net.addLink(h2, s2, port1=0, port2=1)
    net.addLink(h3, s2, port1=0, port2=2)
    net.addLink(s2, r1, port1=3, port2=1, params2={'ip': '10.0.2.254/24'}, addr2='00:00:00:00:0b:01')

    info('Starting Network\n')
    net.build()
    c0.start()
    s1.start([c0])
    s2.start([c0])

    # Assicura la pulizia di eventuali code OVS residue su s1
    os.system("ovs-vsctl clear port s1-eth3 qos 2>/dev/null")
    os.system("ovs-vsctl --all destroy qos 2>/dev/null")
    os.system("ovs-vsctl --all destroy queue 2>/dev/null")

    info('Running CLI\n')
    CLI(net)

    info('Stopping Network\n')
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run_topo_noqos()