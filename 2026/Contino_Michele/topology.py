from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info


def create_network():

    # Creazione rete Mininet
    net = Mininet(
        controller=RemoteController,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True
    )

    # Controller Ryu
    c0 = net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6653
    )

    # Host
    h1 = net.addHost("h1", ip="10.0.0.1/24")  # attacker
    h2 = net.addHost("h2", ip="10.0.0.2/24")  # host normale
    h3 = net.addHost("h3", ip="10.0.0.3/24")  # server

    # Switch OpenFlow
    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    s2 = net.addSwitch("s2", protocols="OpenFlow13")
    s3 = net.addSwitch("s3", protocols="OpenFlow13")
    s4 = net.addSwitch("s4", protocols="OpenFlow13")

    # Collegamenti host-switch
    net.addLink(h1, s1, bw=100, delay="1ms", port2=1)
    net.addLink(h2, s2, bw=100, delay="1ms", port2=1)
    net.addLink(h3, s4, bw=100, delay="1ms", port2=2)

    # Collegamenti tra switch
    net.addLink(
        s1, s3,
        bw=20,
        delay="5ms",
        port1=2,
        port2=1
    )

    net.addLink(
        s2, s3,
        bw=20,
        delay="5ms",
        port1=2,
        port2=2
    )

    # Link condiviso dai due flussi
    net.addLink(
        s3, s4,
        bw=10,
        delay="10ms",
        max_queue_size=100,
        port1=3,
        port2=1
    )

    info("*** Avvio rete\n")

    net.build()
    c0.start()

    for switch in [s1, s2, s3, s4]:
        switch.start([c0])

    info("*** Rete avviata\n")
    info("*** h1 attacker - h2 client - h3 server\n")

    CLI(net)

    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    create_network()