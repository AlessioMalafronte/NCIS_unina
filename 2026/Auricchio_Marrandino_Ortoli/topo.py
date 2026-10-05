from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch, Node
from mininet.cli import CLI
from mininet.log import setLogLevel, info
import os

class LinuxRouter(Node):
    def config(self, **params):
        super(LinuxRouter, self).config(**params)
        self.cmd('sysctl net.ipv4.ip_forward=1')

    def terminate(self):
        self.cmd('sysctl net.ipv4.ip_forward=0')
        super(LinuxRouter, self).terminate()

def run_topology():
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch)

    info('Adding Controller\n')
    c0 = net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)

    info('Adding Router\n')
    r1 = net.addHost('r1', cls=LinuxRouter, ip='10.0.1.254/24', mac='00:00:00:00:0a:01')

    info('Adding Switches\n')
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')

    info('Adding Hosts\n')
    # LAN A
    h1 = net.addHost('h1', ip='10.0.1.1/24', mac='00:00:00:00:01:01', defaultRoute='via 10.0.1.254')
    m1 = net.addHost('m1', ip='10.0.1.2/24', mac='00:00:00:00:01:02', defaultRoute='via 10.0.1.254')
    # LAN B
    h2 = net.addHost('h2', ip='10.0.2.1/24', mac='00:00:00:00:02:01', defaultRoute='via 10.0.2.254')
    h3 = net.addHost('h3', ip='10.0.2.2/24', mac='00:00:00:00:02:02', defaultRoute='via 10.0.2.254')

    # Salva i PID degli host per permettere ad altri script di agganciarsi
    with open('/tmp/mininet_pids.env', 'w') as f:
        for host in net.hosts:
            f.write(f"{host.name}={host.pid}\n")

    info('Creating Links\n')
    # LAN A
    net.addLink(h1, s1, port1=0, port2=1)
    net.addLink(m1, s1, port1=0, port2=2)
    net.addLink(s1, r1, port1=3, port2=0, params2={'ip': '10.0.1.254/24'}) # s1-eth3 to r1-eth0

    # LAN B
    net.addLink(h2, s2, port1=0, port2=1)
    net.addLink(h3, s2, port1=0, port2=2)
    net.addLink(s2, r1, port1=3, port2=1, params2={'ip': '10.0.2.254/24'}, addr2='00:00:00:00:0b:01') # s2-eth3 to r1-eth1

    info('Starting Network\n')
    net.build()
    c0.start()
    s1.start([c0])
    s2.start([c0])

    # QoS Configuration on s1 towards r1 (s1-eth3)
    info('Configuring QoS on s1-eth3 (Bottleneck 10Mbit/s, Q0=3M min, Q1=7M min)\n')
    # Clear existing QoS
    os.system("ovs-vsctl clear port s1-eth3 qos")
    os.system("ovs-vsctl --all destroy qos")
    os.system("ovs-vsctl --all destroy queue")
    
    # Create QoS and Queues
    # max-rate=10000000 (10 Mbps)
    os.system("ovs-vsctl set port s1-eth3 qos=@newqos -- "
              "--id=@newqos create qos type=linux-htb other-config:max-rate=10000000 queues=0=@q0,1=@q1 -- "
              "--id=@q0 create queue other-config:min-rate=3000000 other-config:max-rate=10000000 -- "
              "--id=@q1 create queue other-config:min-rate=7000000 other-config:max-rate=10000000")

    #Imposta il limite rigido a 10 pacchetti per entrambe le code HTB
    # OVS assegna di default la classe 1:1 alla coda 0 e 1:2 alla coda 1
    os.system("tc qdisc add dev s1-eth3 parent 1:1 handle 10: pfifo limit 10 2>/dev/null || tc qdisc change dev s1-eth3 parent 1:1 handle 10: pfifo limit 10")
    os.system("tc qdisc add dev s1-eth3 parent 1:2 handle 20: pfifo limit 10 2>/dev/null || tc qdisc change dev s1-eth3 parent 1:2 handle 20: pfifo limit 10")

    info('Running CLI\n')
    CLI(net)

    info('Stopping Network\n')
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run_topology()
