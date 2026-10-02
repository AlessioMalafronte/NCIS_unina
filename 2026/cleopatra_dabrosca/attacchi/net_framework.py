from mininet.net import Mininet, Host
from mininet.node import OVSKernelSwitch, RemoteController
from mininet.link import TCLink, Link
import re


class NetworkBuilder:
    def __init__(self):
        self.net = Mininet(controller=RemoteController, link=TCLink)
        self.c1 = self.net.addController("c1", controller=RemoteController, ip="127.0.0.1", port=6633)
        self.hosts = dict[str, Host]()
        self.switches = dict[str, OVSKernelSwitch]()

    def add_host(self, name: str, mac: str, ip: str) -> Host:
        host = self.net.addHost(name, mac=mac, ip=ip)
        self.hosts[name] = host
        return host

    def add_switch(self, name: str) -> OVSKernelSwitch:
        switch = self.net.addSwitch(name, cls=OVSKernelSwitch, protocols="OpenFlow13")
        self.switches[name] = switch
        return switch

    def add_link(self, node1_name: str, node2_name: str, bandwidth: int, delay: int) -> Link:
        node1 = self.net.get(node1_name)
        node2 = self.net.get(node2_name)
        link = self.net.addLink(node1, node2, bw = bandwidth, delay = f"{delay}ms")
        return link

    def build_topology(self):
        raise NotImplementedError()

    def start(self):
        self.build_topology()
        self.net.start()
        return self.net

    def stop(self):
        self.net.stop()


class MininetTestSuite:
    def __init__(self, net: Mininet):
        self.net = net

    def ping_test(self, src_name: str, dst_name: str, timeout: int = 1, formatted: bool = True) -> tuple[str, float|str, float|str, float|str, float|str]:
        src = self.net.get(src_name)
        dst = self.net.get(dst_name)
        details = self.net.pingFull([src, dst], timeout=timeout)
        _, _, stats = details[0]
        packets_sent, packets_received, min_ms, avg_ms, max_ms, mdev_ms = stats

        packets_loss = (packets_sent - packets_received) / packets_sent
        packets_loss = f"{packets_loss*100:.2f}%"

        if formatted:
            format_function = lambda value: f"{value:.2f}" + " ms"
            min_ms = format_function(min_ms)
            avg_ms = format_function(avg_ms)
            max_ms = format_function(max_ms)
            mdev_ms = format_function(mdev_ms)

        return packets_loss, min_ms, avg_ms, max_ms, mdev_ms

    def tcp_test(self, client_name: str, server_name: str, server_port: int, bandwidth: float = 3.0, duration: int = 10) -> str:
        server = self.net.getNodeByName(server_name)
        result = self.run_cmd(client_name, "iperf -c {} -p {} -b {}M -l 32k -t {}".format(server.IP(), server_port, bandwidth, duration))
        print(result)
        match = re.search(r"([\d.]+)\s+([KMG])bits/sec", result)
        if match is None:
            return "ERROR"
        value = float(match.group(1))
        unit = match.group(2)

        if unit == 'G':
            value *= 1000.0
        elif unit == 'K':
            value /= 1000.0

        throughput = f"{value:.2f} Mbits/sec"

        return throughput

    def rump_up_tcp_test(self, client_name: str, server_name: str, server_port: int,
                         start_mbps: float, end_mbps: float, step_duration: int) -> list[str]:
        results = list[str]()

        current_mbps = start_mbps
        while current_mbps <= end_mbps:
            result = self.tcp_test(client_name, server_name, server_port, current_mbps, step_duration)
            results.append(result)
            if current_mbps == end_mbps:
                break
            else:
                current_mbps = min(2*current_mbps, end_mbps)
        
        return results


    def run_cmd(self, host_name: str, command: str) -> str:
        host = self.net.getNodeByName(host_name)
        output = host.cmd(command)
        return output


    def wait_output(self, host_name: str) -> None:
        host = self.net.getNodeByName(host_name)
        if host.waiting:
            host.waitOutput()


    def test_suite(self):
        raise  NotImplementedError()

    
