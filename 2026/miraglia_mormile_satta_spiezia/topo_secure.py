#!/usr/bin/python

#systemctl start ovsdb-server.service ovs-vswitchd.service gortr

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import Node
from mininet.log import setLogLevel, info
from mininet.cli import CLI

class OSPFRouter(Node):
    def __init__(self, name, **kwargs):
        kwargs['privateDirs'] = [
            '/usr/local/etc/frr', '/run/frr', 
            '/usr/local/var/run/frr', '/usr/local/var/lib/frr'
        ]
        super(OSPFRouter, self).__init__(name, **kwargs)

    def config(self, **params):
        super(OSPFRouter, self).config(**params)
        self.cmd('sysctl net.ipv4.ip_forward=1')
        networks = params.get('networks', [])
        
        self.cmd('mkdir -p /usr/local/etc/frr /run/frr /usr/local/var/run/frr /usr/local/var/lib/frr')
        
        self.cmd('echo "zebra=yes" > /usr/local/etc/frr/daemons')
        self.cmd('echo "ospfd=yes" >> /usr/local/etc/frr/daemons')
        self.cmd('echo "bgpd=no" >> /usr/local/etc/frr/daemons')
        self.cmd('echo "vtysh_enable=yes" >> /usr/local/etc/frr/daemons')
        
        self.cmd('echo "service integrated-vtysh-config" > /usr/local/etc/frr/vtysh.conf')
        
        self.cmd(f'echo "hostname {self.name}" > /usr/local/etc/frr/frr.conf')
        self.cmd('echo "password en" >> /usr/local/etc/frr/frr.conf')
        self.cmd('echo "enable password en" >> /usr/local/etc/frr/frr.conf')
        
        ospf_intfs = params.get('ospf_intfs', [])
        for intf in ospf_intfs:
            self.cmd(f'echo "interface {intf}" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " ip ospf hello-interval 1" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " ip ospf dead-interval 4" >> /usr/local/etc/frr/frr.conf')
        
        self.cmd('echo "router ospf" >> /usr/local/etc/frr/frr.conf')
        for net in networks:
            self.cmd(f'echo " network {net} area 0" >> /usr/local/etc/frr/frr.conf')
            
        self.cmd('chown -R frr:frr /usr/local/etc/frr /run/frr /usr/local/var/run/frr /usr/local/var/lib/frr')

    def start_frr(self):
        self.cmd('/usr/local/sbin/zebra -d -f /usr/local/etc/frr/frr.conf -z /run/frr/zserv.api -i /run/frr/zebra.pid -u root -g root')
        self.cmd('/usr/local/sbin/ospfd -d -f /usr/local/etc/frr/frr.conf -z /run/frr/zserv.api -i /run/frr/ospfd.pid -u root -g root')

    def terminate(self):
        self.cmd('kill -9 `cat /run/frr/zebra.pid` 2>/dev/null || true')
        self.cmd('kill -9 `cat /run/frr/ospfd.pid` 2>/dev/null || true')
        self.cmd('sysctl net.ipv4.ip_forward=0')
        super(OSPFRouter, self).terminate()

class RIPRouter(Node):
    def __init__(self, name, **kwargs):
        kwargs['privateDirs'] = [
            '/usr/local/etc/frr', 
            '/run/frr', 
            '/usr/local/var/run/frr', 
            '/usr/local/var/lib/frr'
        ]
        super(RIPRouter, self).__init__(name, **kwargs)

    def config(self, **params):
        super(RIPRouter, self).config(**params)
        self.cmd('sysctl net.ipv4.ip_forward=1')
        networks = params.get('networks', [])

        self.cmd('mkdir -p /usr/local/etc/frr /run/frr /usr/local/var/run/frr /usr/local/var/lib/frr')

        self.cmd('echo "zebra=yes" > /usr/local/etc/frr/daemons')
        self.cmd('echo "mgmtd=yes" >> /usr/local/etc/frr/daemons')
        self.cmd('echo "ripd=yes" >> /usr/local/etc/frr/daemons')
        self.cmd('echo "ospfd=no" >> /usr/local/etc/frr/daemons')
        self.cmd('echo "bgpd=no" >> /usr/local/etc/frr/daemons')
        self.cmd('echo "vtysh_enable=yes" >> /usr/local/etc/frr/daemons')

        self.cmd('echo "service integrated-vtysh-config" > /usr/local/etc/frr/vtysh.conf')

        self.cmd(f'echo "hostname {self.name}" > /usr/local/etc/frr/frr.conf')
        self.cmd('echo "password en" >> /usr/local/etc/frr/frr.conf')
        self.cmd('echo "enable password en" >> /usr/local/etc/frr/frr.conf')
        self.cmd('echo "router rip" >> /usr/local/etc/frr/frr.conf')
        self.cmd('echo " version 2" >> /usr/local/etc/frr/frr.conf')
        for net in networks:
            self.cmd(f'echo " network {net}" >> /usr/local/etc/frr/frr.conf')

        self.cmd('chown -R frr:frr /usr/local/etc/frr /run/frr /usr/local/var/run/frr /usr/local/var/lib/frr 2>/dev/null || true')

    def start_frr(self):

        self.cmd('/usr/local/sbin/mgmtd -d -i /run/frr/mgmtd.pid -u root -g root')
        self.cmd('/usr/local/sbin/zebra -d -z /run/frr/zserv.api -i /run/frr/zebra.pid -u root -g root')
        self.cmd('/usr/local/sbin/ripd -d -z /run/frr/zserv.api -i /run/frr/ripd.pid -u root -g root')
        self.cmd('/usr/local/bin/vtysh -f /usr/local/etc/frr/frr.conf')

        '''
        # Per il debug
        if out_mgmtd.strip():
            print(f"[Debug MGMTD su {self.name}]: {out_mgmtd.strip()}")
        if out_ripd.strip():
            print(f"[Debug RIPD su {self.name}]: {out_ripd.strip()}")
        if out_vtysh.strip():
            print(f"[Debug VTYSH su {self.name}]: {out_vtysh.strip()}")
        '''
    def terminate(self):
        self.cmd('kill -9 `cat /run/frr/ripd.pid` 2>/dev/null || true')
        self.cmd('kill -9 `cat /run/frr/zebra.pid` 2>/dev/null || true')
        self.cmd('kill -9 `cat /run/frr/mgmtd.pid` 2>/dev/null || true')
        self.cmd('sysctl net.ipv4.ip_forward=0')
        super(RIPRouter, self).terminate()

class BorderRouter(Node):
    def __init__(self, name, **kwargs):
        kwargs['privateDirs'] = [
            '/usr/local/etc/frr', 
            '/run/frr', 
            '/usr/local/var/run/frr', 
            '/usr/local/var/lib/frr'
        ]
        super(BorderRouter, self).__init__(name, **kwargs)

    def config(self, **params):
        super(BorderRouter, self).config(**params)
        self.cmd('sysctl net.ipv4.ip_forward=1')
        
        self.igp = params.get('igp', 'ospf')
        igp_networks = params.get('networks', [])
        ospf_intfs = params.get('ospf_intfs', [])
        
        asn = params.get('asn')
        router_id = params.get('router_id')
        bgp_neighbors = params.get('bgp_neighbors', [])
        bgp_networks = params.get('bgp_networks', [])
        rpki_server = params.get('rpki_server')

        self.cmd('mkdir -p /usr/local/etc/frr /run/frr /usr/local/var/run/frr /usr/local/var/lib/frr')

        ospf_flag = "yes" if self.igp == 'ospf' else "no"
        rip_flag = "yes" if self.igp == 'rip' else "no"
        
        self.cmd('echo "zebra=yes" > /usr/local/etc/frr/daemons')
        self.cmd('echo "mgmtd=yes" >> /usr/local/etc/frr/daemons')
        self.cmd('echo "bgpd=yes" >> /usr/local/etc/frr/daemons')
        self.cmd('echo "bgpd_options=\\"-M rpki\\"" >> /usr/local/etc/frr/daemons')

        self.cmd(f'echo "ospfd={ospf_flag}" >> /usr/local/etc/frr/daemons')
        self.cmd(f'echo "ripd={rip_flag}" >> /usr/local/etc/frr/daemons')
        self.cmd('echo "vtysh_enable=yes" >> /usr/local/etc/frr/daemons')

        self.cmd('echo "service integrated-vtysh-config" > /usr/local/etc/frr/vtysh.conf')

        self.cmd(f'echo "hostname {self.name}" > /usr/local/etc/frr/frr.conf')
        self.cmd('echo "password en" >> /usr/local/etc/frr/frr.conf')
        self.cmd('echo "enable password en" >> /usr/local/etc/frr/frr.conf')

        if self.igp == 'ospf':
            for intf in ospf_intfs:
                self.cmd(f'echo "interface {intf}" >> /usr/local/etc/frr/frr.conf')
                self.cmd('echo " ip ospf hello-interval 1" >> /usr/local/etc/frr/frr.conf')
                self.cmd('echo " ip ospf dead-interval 4" >> /usr/local/etc/frr/frr.conf')

            self.cmd('echo "router ospf" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " redistribute bgp" >> /usr/local/etc/frr/frr.conf')
            for net in igp_networks:
                self.cmd(f'echo " network {net} area 0" >> /usr/local/etc/frr/frr.conf')

        elif self.igp == 'rip':
            self.cmd('echo "router rip" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " version 2" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " default-metric 1" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " redistribute bgp" >> /usr/local/etc/frr/frr.conf')
            for net in igp_networks:
                self.cmd(f'echo " network {net}" >> /usr/local/etc/frr/frr.conf')

        # Configurazione BGP con server RPKI -> maggiore sicurezza
        if asn:
            self.cmd(f'echo "router bgp {asn}" >> /usr/local/etc/frr/frr.conf')
            if router_id:
        # Numero di AS, id del router, vicini e rotte preannunciate
                self.cmd(f'echo " bgp router-id {router_id}" >> /usr/local/etc/frr/frr.conf')

            self.cmd('echo " no bgp ebgp-requires-policy" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " no bgp network import-check" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " timers bgp 3 9" >> /usr/local/etc/frr/frr.conf')

            for peer_ip, peer_asn in bgp_neighbors:
                self.cmd(f'echo " neighbor {peer_ip} remote-as {peer_asn}" >> /usr/local/etc/frr/frr.conf')
                self.cmd(f'echo " neighbor {peer_ip} timers connect 5" >> /usr/local/etc/frr/frr.conf')

            self.cmd('echo " address-family ipv4 unicast" >> /usr/local/etc/frr/frr.conf')
            for net in bgp_networks:
                self.cmd(f'echo "  network {net}" >> /usr/local/etc/frr/frr.conf')
            
            self.cmd('echo "  redistribute connected" >> /usr/local/etc/frr/frr.conf')
            self.cmd(f'echo "  redistribute {self.igp}" >> /usr/local/etc/frr/frr.conf')
            
            for peer_ip, peer_asn in bgp_neighbors:
                self.cmd(f'echo "  neighbor {peer_ip} route-map PERMIT-OUT out" >> /usr/local/etc/frr/frr.conf')
                if rpki_server:
                    self.cmd(f'echo "  neighbor {peer_ip} route-map RPKI-DEFENSE in" >> /usr/local/etc/frr/frr.conf')
            
            self.cmd('echo " exit-address-family" >> /usr/local/etc/frr/frr.conf')
        
        if rpki_server:
            # Connessione al server gortr
            self.cmd('echo "rpki" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " rpki polling_period 15" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " rpki retry_interval 15" >> /usr/local/etc/frr/frr.conf')
            self.cmd(f'echo " rpki cache tcp {rpki_server} 8282 preference 1" >> /usr/local/etc/frr/frr.conf')
            
            self.cmd('echo " exit" >> /usr/local/etc/frr/frr.conf')
            
            self.cmd('echo "route-map RPKI-DEFENSE deny 10" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo " match rpki invalid" >> /usr/local/etc/frr/frr.conf')
            self.cmd('echo "route-map RPKI-DEFENSE permit 20" >> /usr/local/etc/frr/frr.conf') 

        if asn:
            self.cmd('echo "route-map PERMIT-OUT permit 10" >> /usr/local/etc/frr/frr.conf')

        self.cmd('chown -R frr:frr /usr/local/etc/frr /run/frr /usr/local/var/run/frr /usr/local/var/lib/frr 2>/dev/null || true')

    def start_frr(self):
        self.cmd('/usr/local/sbin/mgmtd -d -i /run/frr/mgmtd.pid -u root -g root')
        self.cmd('/usr/local/sbin/zebra -d -z /run/frr/zserv.api -i /run/frr/zebra.pid -u root -g root')
        
        rpki_flag = "-M rpki" if self.params.get('rpki_server') else ""
        self.cmd(f'/usr/local/sbin/bgpd {rpki_flag} -d -z /run/frr/zserv.api -i /run/frr/bgpd.pid -u root -g root')

        if self.igp == 'ospf':
            self.cmd('/usr/local/sbin/ospfd -d -z /run/frr/zserv.api -i /run/frr/ospfd.pid -u root -g root')
            self.cmd('while [ ! -S /usr/local/var/run/frr/bgpd.vty ] || [ ! -S /usr/local/var/run/frr/ospfd.vty ]; do sleep 0.05; done')
        elif self.igp == 'rip':
            self.cmd('/usr/local/sbin/ripd -d -z /run/frr/zserv.api -i /run/frr/ripd.pid -u root -g root')
            self.cmd('while [ ! -S /usr/local/var/run/frr/bgpd.vty ] || [ ! -S /usr/local/var/run/frr/ripd.vty ]; do sleep 0.05; done')
        
        out_vtysh = self.cmd('/usr/local/bin/vtysh -f /usr/local/etc/frr/frr.conf')
        '''
        # Debug
        if out_vtysh.strip():
            print(f"[Debug VTYSH su {self.name}]: {out_vtysh.strip()}")
        '''

    def terminate(self):
        for daemon in ['bgpd', 'ospfd', 'ripd', 'zebra', 'mgmtd']:
            self.cmd(f'kill -9 `cat /run/frr/{daemon}.pid` 2>/dev/null || true')
        self.cmd('sysctl net.ipv4.ip_forward=0')
        super(BorderRouter, self).terminate()

class NetworkTopo(Topo):
    def build(self, **_opts):
        # Autonomous System #1
        # Aggiungiamo i router specificando le reti da includere in OSPF
        r1 = self.addHost('r1', cls=OSPFRouter, ip='10.0.0.1/24', 
                          networks=['10.0.0.0/24', '10.100.0.0/24', '10.200.0.0/24'], ospf_intfs=['r1-eth2', 'r1-eth3'])
        r2 = self.addHost('r2', cls=OSPFRouter, ip='10.1.0.1/24', 
                          networks=['10.1.0.0/24', '10.100.0.0/24', '10.50.0.0/24'], ospf_intfs=['r2-eth2', 'r2-eth3'])
        r3 = self.addHost('r3', cls=OSPFRouter, ip = '10.2.0.1/24',
                          networks=['10.2.0.0/24', '10.200.0.0/24', '10.50.0.0/24', '10.150.0.0/24'], ospf_intfs=['r3-eth2', 'r3-eth3', 'r3-eth4'])
        
        # Aggiungiamo gli switch
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')

        # Link Host-Switch
        self.addLink(s1, r1, intfName2='r1-eth1', params2={'ip': '10.0.0.1/24'})
        self.addLink(s2, r2, intfName2='r2-eth1', params2={'ip': '10.1.0.1/24'})
        self.addLink(s3, r3, intfName2='r3-eth1', params2={'ip': '10.2.0.1/24'})

        # Link Router-Router
        self.addLink(r1, r2, 
                     intfName1='r1-eth2', intfName2='r2-eth2',
                     params1={'ip': '10.100.0.1/24'}, params2={'ip': '10.100.0.2/24'})
        self.addLink(r1, r3,
                     intfName1='r1-eth3', intfName2='r3-eth2',
                     params1={'ip': '10.200.0.1/24'}, params2={'ip': '10.200.0.2/24'})
        self.addLink(r2, r3,
                     intfName1='r2-eth3', intfName2='r3-eth3',
                     params1={'ip': '10.50.0.1/24'}, params2={'ip': '10.50.0.2/24'})

        # Aggiungiamo gli host con le route di default
        host1 = self.addHost(name='h1', ip='10.0.0.251/24', defaultRoute='via 10.0.0.1')
        host2 = self.addHost(name='h2', ip='10.1.0.252/24', defaultRoute='via 10.1.0.1')
        host3 = self.addHost(name='h3', ip='10.0.0.252/24', defaultRoute='via 10.0.0.1')
        host4 = self.addHost(name='h4', ip='10.2.0.251/24', defaultRoute='via 10.2.0.1')

        # Connettiamo gli host agli switch
        self.addLink(host1, s1)
        self.addLink(host3, s1)
        self.addLink(host2, s2)
        self.addLink(host4, s3)

        # Configurazione router di frontiera
        rf1 = self.addHost('rf1', cls=BorderRouter, ip='10.150.0.1/24',
                          igp='ospf',
                          networks=['10.150.0.0/24', '20.20.0.0/24', '30.30.0.0/24', '40.40.0.0/24'],
                          ospf_intfs=['rf1-eth1'],
                          asn=100,
                          router_id='192.168.100.1',
                          bgp_neighbors=[('20.20.0.2', 200), ('30.30.0.2', 300)],
                          rpki_server='40.40.0.2')

        self.addLink(rf1, r3,
                     intfName1='rf1-eth1', intfName2='r3-eth4', 
                     params1={'ip': '10.150.0.1/24'}, params2={'ip': '10.150.0.2/24'})

        # Autonomous System #2

        r4 = self.addHost('r4', cls=RIPRouter, ip='172.16.0.1/24', 
                          networks=['172.16.0.0/24', '172.16.100.0/24', '172.16.200.0/24'])
        r5 = self.addHost('r5', cls=RIPRouter, ip='172.16.1.1/24', 
                          networks=['172.16.1.0/24', '172.16.100.0/24', '172.16.50.0/24'])
        r6 = self.addHost('r6', cls=RIPRouter, ip = '172.16.2.1/24',
                          networks=['172.16.2.0/24', '172.16.200.0/24', '172.16.50.0/24', '172.16.150.0/24'])
        
        s4 = self.addSwitch('s4')
        s5 = self.addSwitch('s5')
        s6 = self.addSwitch('s6')

        self.addLink(s4, r4, intfName2='r4-eth1', params2={'ip': '172.16.0.1/24'})
        self.addLink(s5, r5, intfName2='r5-eth1', params2={'ip': '172.16.1.1/24'})
        self.addLink(s6, r6, intfName2='r6-eth1', params2={'ip': '172.16.2.1/24'})

        self.addLink(r4, r5, 
                     intfName1='r4-eth2', intfName2='r5-eth2',
                     params1={'ip': '172.16.100.1/24'}, params2={'ip': '172.16.100.2/24'})
        self.addLink(r4, r6,
                     intfName1='r4-eth3', intfName2='r6-eth2',
                     params1={'ip': '172.16.200.1/24'}, params2={'ip': '172.16.200.2/24'})
        self.addLink(r5, r6,
                     intfName1='r5-eth3', intfName2='r6-eth3',
                     params1={'ip': '172.16.50.1/24'}, params2={'ip': '172.16.50.2/24'})
        
        host5 = self.addHost(name='h5', ip='172.16.0.251/24', defaultRoute='via 172.16.0.1')
        host6 = self.addHost(name='h6', ip='172.16.1.251/24', defaultRoute='via 172.16.1.1')
        host7 = self.addHost(name='h7', ip='172.16.0.252/24', defaultRoute='via 172.16.0.1')
        host8 = self.addHost(name='h8', ip='172.16.2.251/24', defaultRoute='via 172.16.2.1')
        
        self.addLink(host5, s4)
        self.addLink(host7, s4)
        self.addLink(host6, s5)
        self.addLink(host8, s6)

        rf2 = self.addHost('rf2', cls=BorderRouter, ip='172.16.150.1/24',
                          igp='rip',
                          networks=['172.16.150.0/24', '20.20.0.0/24'],
                          asn=200,
                          router_id='192.168.100.2',
                          bgp_neighbors=[('20.20.0.1', 100)])

        self.addLink(rf2, r6,
                     intfName1='rf2-eth1', intfName2='r6-eth4', 
                     params1={'ip': '172.16.150.1/24'}, params2={'ip': '172.16.150.2/24'})
        

        # Autonomous System dell'attore malevolo
        rf3 = self.addHost('rf3', cls=BorderRouter, ip='192.168.0.1/24',
                          networks=['192.168.0.0/24', '30.30.0.0/24'],
                          asn=300,
                          router_id='192.168.100.3',
                          bgp_neighbors=[('30.30.0.1', 100)])
        
        hostm = self.addHost(name='JD', ip='192.168.0.251/24', defaultRoute='via 192.168.0.1')
        s7 = self.addSwitch('s7')
        self.addLink(hostm, s7)
        self.addLink(s7, rf3, intfName2='rf3-eth1', params2={'ip': '192.168.0.1/24'})
        
        # Collegamenti tra i router di frontiera
        self.addLink(rf1, rf2,
                    intfName1='rf1-eth2', intfName2='rf2-eth2',
                    params1={'ip': '20.20.0.1/24'}, params2={'ip': '20.20.0.2/24'})
        self.addLink(rf1, rf3,
                    intfName1='rf1-eth3', intfName2='rf3-eth2',
                    params1={'ip': '30.30.0.1/24'}, params2={'ip': '30.30.0.2/24'})
        
        # Server RPKI
        rpki = self.addHost('rpki', ip='40.40.0.2/24', defaultRoute='via 40.40.0.1')
        self.addLink(rf1, rpki, intfName1='rf1-eth4', intfName2='rpki-eth0', params1={'ip':'40.40.0.1/24'}, params2={'ip':'40.40.0.2/24'})

def run():
    topo = NetworkTopo()
    net = Mininet(topo=topo)
    net.start()

    info("*** Avvio server RPKI su rpki ***\n")
    net['rpki'].cmd('gortr -bind 40.40.0.2:8282 -verify=false -metrics.addr="" -cache roa.json > /tmp/gortr.log 2>&1 &')

    info("*** Avvio demoni FRR sui router...\n")
    net['r1'].start_frr()
    net['r2'].start_frr()
    net['r3'].start_frr()
    net['r4'].start_frr()
    net['r5'].start_frr()
    net['r6'].start_frr()
    net['rf1'].start_frr()
    net['rf2'].start_frr()
    net['rf3'].start_frr()

    CLI(net)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run()