import subprocess
import time
from mininet.log import setLogLevel, info
import os

def load_pids():
    pids = {}
    if not os.path.exists('/tmp/mininet_pids.env'):
        print("[!] Errore: File /tmp/mininet_pids.env non trovato. Avvia prima la topologia!")
        exit(1)
    with open('/tmp/mininet_pids.env') as f:
        for line in f:
            line = line.strip()
            if '=' in line:
                name, pid = line.split('=')
                pids[name] = pid
    return pids

def run_in_host(pid, cmd):
    """Esegue un comando dentro il namespace dell'host tramite mnexec con sudo"""
    return subprocess.run(f"sudo mnexec -a {pid} {cmd}", shell=True, capture_output=True, text=True)

def main():
    os.makedirs('results', exist_ok=True)

    # Richiesta modalità di esecuzione
    choice = input("Quale topologia e in esecuzione? [Q = QoS / N = NoQoS]: ").strip().upper()
    is_qos = (choice == 'Q')

    if is_qos:
        h2_file = 'results/h2_iperf.txt'
        h3_file = 'results/h3_iperf.txt'
        print("[*] Modalita selezionata: QoS attiva.")
    else:
        h2_file = 'results/noqos_h2_iperf.txt'
        h3_file = 'results/noqos_h3_iperf.txt'
        print("[*] Modalita selezionata: No-QoS (baseline).")

    pids = load_pids()
    print(f"[*] Agganciato alla topologia attiva: {pids}")
    
    # Pulizia preliminare server precedenti
    run_in_host(pids['h2'], 'killall -9 iperf3 2>/dev/null')
    run_in_host(pids['h3'], 'killall -9 iperf3 2>/dev/null')
    time.sleep(1)

    # 1. Avvia iperf3 server su h2 e h3
    print("[*] Avvio server iperf3...")
    run_in_host(pids['h2'], 'iperf3 -s -p 5201 -D')
    run_in_host(pids['h3'], 'iperf3 -s -p 5202 -D')
    time.sleep(1)

    # 2. Avvia iperf3 client su h1
    print(f"[*] Generazione flussi UDP (10s) -> Salvataggio in {h2_file} e {h3_file}...")
    p_h3 = subprocess.Popen(f"sudo mnexec -a {pids['h1']} iperf3 -c 10.0.2.2 -u -b 12M -p 5202 -t 10 > {h3_file} 2>&1", shell=True)
    run_in_host(pids['h1'], f"iperf3 -c 10.0.2.1 -u -b 2M -p 5201 -t 10 > {h2_file} 2>&1")
    p_h3.wait()

    # 3. Lancia lo spoofing su m1
    print("[*] Esecuzione test ARP Spoofing da m1...")
    run_in_host(pids['m1'], 'python3 test_arp.py > results/arp_attack.txt')
    
    # 4. Verifica il blocco di m1
    res = run_in_host(pids['m1'], 'ping -c 2 10.0.1.1')
    with open('results/ping_after_attack.txt', 'w') as f:
        f.write(res.stdout)
    
    # 5. Raccolta statistiche code solo se siamo in QoS
    if is_qos:
        print("[*] Raccolta statistiche code OVS...")
        stats = os.popen('sudo ovs-ofctl queue-stats s1 3 -O OpenFlow13').read()
        with open('./results/ovs_queue_stats.txt', 'w') as f:
            f.write(stats)

    # Pulizia server iperf3
    run_in_host(pids['h2'], 'killall -9 iperf3 2>/dev/null')
    run_in_host(pids['h3'], 'killall -9 iperf3 2>/dev/null')

    print("[+] Test completato con successo.")

if __name__ == '__main__':
    setLogLevel('info')
    main()