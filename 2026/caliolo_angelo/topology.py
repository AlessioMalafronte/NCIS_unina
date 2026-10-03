#!/usr/bin/python
import threading
import random
import time
from mininet.log import setLogLevel, info
from mininet.topo import Topo
from mininet.net import Mininet, CLI
from mininet.node import OVSKernelSwitch, Host
from mininet.link import TCLink, Link
from mininet.node import RemoteController #Controller
from mininet.util import dumpNodeConnections

class Environment(object):
    def __init__(self):
        
        # Creiamo una rete in Mininet, specificando un controller remoto e definendo i Link con interfaccia TC Link
        # TCLink (Traffic Control Link), che permette di emulare parametri fisici di rete
        # (come limitazione di banda, delay e packet loss) tramite lo strumento di sistema 'tc'.
        "Create a network."
        self.net = Mininet(controller=RemoteController, link=TCLink) 

        try:
            # Aggiungiamo un controller remoto alla rete
            # Il controller remoto fornito dalla libreria agisce indipendentemente da Mininet
            info("*** Starting controllers\n")
            c1 = self.net.addController( 'C1', controller=RemoteController, port=6633) #Controller 1: Protegge dai flussi in eccesso
            c1.start()

            # Aggiungiamo gli host 
            info("*** Adding hosts\n")
            self.h1 = self.net.addHost('h1', mac ='00:00:00:00:00:01', ip= '10.0.0.1')
            self.h2 = self.net.addHost('h2', mac ='00:00:00:00:00:02', ip= '10.0.0.2')
            self.h3 = self.net.addHost('h3', mac ='00:00:00:00:00:03', ip= '10.0.0.3')
            self.h4 = self.net.addHost('h4', mac ='00:00:00:00:00:04', ip= '10.0.0.4')
        
            # Aggiungiamo gli switch basati su Open VSwitch
            info("*** Adding switches\n")
            self.s1 = self.net.addSwitch('s1')
            self.s2 = self.net.addSwitch('s2')
            self.s3 = self.net.addSwitch('s3')
            self.s4 = self.net.addSwitch('s4')
            
            # Colleghiamo il tutto, limitando la banda a 10mbps
            info("*** Adding links\n")  
            self.net.addLink(self.h1,self.s1)
            self.net.addLink(self.h4,self.s1)
            self.net.addLink(self.h2,self.s2)
            self.net.addLink(self.h3,self.s4)
            self.net.addLink(self.s1,self.s3,bw=10)
            self.net.addLink(self.s2,self.s3,bw=5)
            self.net.addLink(self.s4,self.s3,bw=15)

            # Costruzione e avvio della topologia
            info("*** Starting network\n")
            self.net.build()
            self.net.start()
            self.net.pingAll()
            self.h3.cmd('iperf -s&') # Per testare il traffico TCP

        except:
            self.net.stop()
        
...
if __name__ == '__main__':

    setLogLevel('info')
    info('starting the environment\n')
    env = Environment()

    info("*** Running CLI\n")
    CLI(env.net)
    env.net.stop()