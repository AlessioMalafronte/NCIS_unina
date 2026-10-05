from mininet.topo import Topo
from mininet.net import Mininet, CLI
from mininet.link import TCLink, Link
from mininet.node import OVSKernelSwitch, Switch, Host, RemoteController


NUM_HOST = 4
NUM_SWITCH = 4
PROTOCOL = 'OpenFlow13'
UPPER_DELAY = '15ms'
LOWER_DELAY = '30ms'
UPPER_BANDWIDTH = 10
LOWER_BANDWIDTH = 1

LINK_PROFILE = {
    'upper_access': {'delay': UPPER_DELAY},
    'lower_access': {'delay': LOWER_DELAY},
    'upper': {'delay': UPPER_DELAY, 'bw': UPPER_BANDWIDTH},
    'lower': {'delay': LOWER_DELAY, 'bw': LOWER_BANDWIDTH},
}


class CustomTopology(Topo):

    def create_switch(self, switch_index: int) -> str:
        switch_name = f's{switch_index}'
        return self.addSwitch(switch_name, cls=OVSKernelSwitch, protocols=PROTOCOL)

    def create_host(self, host_index: int) -> str:
        host_name = f'h{host_index}'
        mac_address = f'00:00:00:00:00:0{host_index}'
        ip_address = f'10.0.0.{host_index}'
        return self.addHost(host_name, mac=mac_address, ip=ip_address)

    def create_link(self, center, edges:list):
        for edge in edges:
            self.addLink(
                center, edge['device'], port1=edges.index(edge) + 1, 
                port2=edge['port'], **edge['profile']
            )

    def build(self):
        # Build Switch
        s1, s2, s3, s4 = [self.create_switch(switch_index + 1) for switch_index in range(NUM_SWITCH)]

        # Build Host
        h1, h2, h3, h4 = [self.create_host(host_index + 1) for host_index in range(NUM_HOST)]

        # Build s1 Link
        s1_edges = [
            {'device': h1, 'port':1, 'profile':LINK_PROFILE['upper_access']},
            {'device': h2, 'port':1, 'profile':LINK_PROFILE['lower_access']},
            {'device': s2, 'port':1, 'profile':LINK_PROFILE['upper']},
            {'device': s3, 'port':1, 'profile':LINK_PROFILE['lower']},
        ]
        self.create_link(s1, s1_edges)

        # Build s4 Link
        s4_edges = [
            {'device': s2, 'port':2, 'profile':LINK_PROFILE['upper']},
            {'device': s3, 'port':2, 'profile':LINK_PROFILE['lower']},
            {'device': h3, 'port':1, 'profile':LINK_PROFILE['upper_access']},
            {'device': h4, 'port':1, 'profile':LINK_PROFILE['lower_access']},
        ]
        self.create_link(s4, s4_edges)


def entry_point():
    topology = CustomTopology()
    network = Mininet(topo=topology, controller=RemoteController, link=TCLink)
    network.addController('c1', controller=RemoteController, ip='127.0.0.1', port=6653)
    print("[SYSTEM] Start network...")
    network.start()
    CLI(network)
    print("[SYSTEM] Stop network...")
    network.stop()


if __name__ == '__main__':
    entry_point ()

