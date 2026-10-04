#!/usr/bin/python
import subprocess
from mininet.log import setLogLevel, info
from mininet.net import Mininet
from mininet.cli import CLI
from mininet.node import OVSKernelSwitch, RemoteController
from mininet.link import TCLink

PREMIUM_MAX = 10000000   # 10 Mbps sul percorso premium
VIDEO_MIN = 6000000      # banda garantita coda video
VOICE_MIN = 2000000      # banda garantita coda voce


def set_qos(iface):
    cmd = (
        'ovs-vsctl -- set port {0} qos=@newqos '
        '-- --id=@newqos create qos type=linux-htb '
        'other-config:max-rate={1} '
        'queues=0=@q0,1=@q1,2=@q2 '
        '-- --id=@q0 create queue other-config:min-rate=100000 other-config:max-rate={1} '
        '-- --id=@q1 create queue other-config:min-rate={2} other-config:max-rate={1} '
        '-- --id=@q2 create queue other-config:min-rate={3} other-config:max-rate={1}'
    ).format(iface, PREMIUM_MAX, VIDEO_MIN, VOICE_MIN)
    subprocess.call(cmd, shell=True)


class Environment(object):
    def __init__(self):
        self.net = Mininet(controller=RemoteController, link=TCLink,
                           autoSetMacs=False, build=False)
        info('*** Adding controller\n')
        self.net.addController('c1', controller=RemoteController,
                               ip='127.0.0.1', port=6633)

        info('*** Adding hosts\n')
        # tenant premium: h1 video, h2 voce, h4 video, h5 voce 
        # tenant standard: h3 e h6 (dati)
        h = {}
        for i in range(1, 7):
            h[i] = self.net.addHost('h%d' % i,
                                    mac='00:00:00:00:00:0%d' % i,
                                    ip='10.0.0.%d/24' % i)

        info('*** Adding switches\n')
        s1 = self.net.addSwitch('s1', cls=OVSKernelSwitch, protocols='OpenFlow13')
        s2 = self.net.addSwitch('s2', cls=OVSKernelSwitch, protocols='OpenFlow13')
        s3 = self.net.addSwitch('s3', cls=OVSKernelSwitch, protocols='OpenFlow13')
        s4 = self.net.addSwitch('s4', cls=OVSKernelSwitch, protocols='OpenFlow13')

        info('*** Adding links\n')
        self.net.addLink(h[1], s1)                          # s1-eth1
        self.net.addLink(h[2], s1)                          # s1-eth2
        self.net.addLink(h[3], s1)                          # s1-eth3
        self.net.addLink(h[4], s2)                          # s2-eth1
        self.net.addLink(h[5], s2)                          # s2-eth2
        self.net.addLink(h[6], s2)                          # s2-eth3
        # percorso premium
        self.net.addLink(s1, s3)                            # s1-eth4, s3-eth1
        self.net.addLink(s3, s2)                            # s3-eth2, s2-eth4
        # percorso standard: 2 Mbps 
        self.net.addLink(s1, s4, bw=2, delay='5ms')         # s1-eth5, s4-eth1
        self.net.addLink(s4, s2, bw=2, delay='5ms')         # s4-eth2, s2-eth5

        info('*** Starting network\n')
        self.net.build()
        self.net.start()

        #evita broadcast sul ciclo s1-s3-s2-s4
        self.net.staticArp()

        info('*** Configuring QoS queues on premium path\n')
        for iface in ['s1-eth4', 's3-eth1', 's3-eth2', 's2-eth4']:
            set_qos(iface)


if __name__ == '__main__':
    setLogLevel('info')
    env = Environment()
    info('*** Running CLI\n')
    CLI(env.net)
    info('*** Cleaning QoS\n')
    subprocess.call('ovs-vsctl --all destroy qos', shell=True)
    subprocess.call('ovs-vsctl --all destroy queue', shell=True)
    env.net.stop()