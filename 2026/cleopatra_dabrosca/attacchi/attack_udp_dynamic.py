import time
from mininet.log import setLogLevel, info
from net_framework import MininetTestSuite
from utils import ReportTable
from topology import BasicTopology


class BasicTest(MininetTestSuite):
    def test_suite(self):
        info("\n*** Avvio server iperf su H3...\n")
        self.run_cmd("h3", "iperf -s -u -p 5001 > /dev/null 2>&1 &")  # SERVER UDP per H1
        self.run_cmd("h3", "iperf -s -p 5002 > /dev/null 2>&1 &")     # SERVER TCP per H2
        time.sleep(2)

        self.net.pingAll()
        time.sleep(2)

        info("\n--- BASELINE ---\n")
        ping_baseline = self.ping_test("h2", "h3")
        throughput_baseline = self.tcp_test("h2", "h3", 5002)

        info("\n--- DoS (UDP DYNAMIC)---\n")
        self.run_cmd("h1", "for i in {1..100}; do iperf -c 10.0.0.3 -u -b 100M -t 0.5 -p 5001; sleep 2; done &")
        time.sleep(5)

        ping_dos = self.ping_test("h2", "h3")
        throughput_dos = " -> ".join(self.rump_up_tcp_test("h2", "h3", 5002, 1.0, 6.0, 20))

        self.run_cmd("h1", "killall -9 iperf")
        self.run_cmd("h2", "killall -9 iperf")
        self.run_cmd("h3", "killall -9 iperf")

        report = ReportTable()
        report.addRow(["Packet Loss", ping_baseline[0], ping_dos[0]])
        report.addRow(["Latenza Minima (RTT)", ping_baseline[1], ping_dos[1]])
        report.addRow(["Latenza Media (RTT)", ping_baseline[2], ping_dos[2]])
        report.addRow(["Latenza Massima (RTT)", ping_baseline[3], ping_dos[3]])
        report.addRow(["Latenza MDEV (RTT)", ping_baseline[4], ping_dos[4]])
        report.addRow(["Throughput (TCP)", throughput_baseline, throughput_dos])
        print(report)



if __name__ == "__main__":
    setLogLevel("info")

    builder = BasicTopology()
    net = builder.start()

    try:
        tester = BasicTest(net)
        tester.test_suite()
    finally:
        builder.stop()