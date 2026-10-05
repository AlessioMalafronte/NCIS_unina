# Progetto SDN - Mininet, Ryu, QoS HTB e Sicurezza DAI

Progetto per il corso di **Network and Cloud Infrastructures** (A.A. 2025/2026, Prof. Giorgio Ventre).  
L'infrastruttura emula una rete industriale multi-subnet basata su OpenFlow 1.3 con due obiettivi primari:
1. **Quality of Service (QoS):** garanzia deterministica di banda per telemetria critica SCADA rispetto a traffico concorrente di bulk transfer su canale condiviso (bottleneck a 10 Mbps).
2. **Sicurezza sul Piano Dati:** rilevamento e mitigazione real-time di attacchi Man-in-the-Middle tramite **Dynamic ARP Inspection (DAI)** e isolamento hardware dell'attaccante.

---

## Componenti del Gruppo di Lavoro

- **Antonio Auricchio** - Matr. DE9000022
- **Salvatore Marrandino** - Matr. DE9000118
- **Alessandro Ortoli** - Matr. DE9000038

---

## Struttura dei File
```bash
.
├── controller.py              # Controller Ryu OpenFlow 1.3 (L2 Learning, QoS HTB Coda 0/1, DAI)
├── controllerSenzaQoS.py      # Controller Ryu di baseline (L2 Learning, DAI, no SetQueue)
├── topo.py                    # Topologia Mininet con router L3, OVS e code HTB su s1-eth3
├── topoNoQos.py               # Topologia Mininet speculare senza classi di servizio HTB
├── test_script.py             # Suite unificata di test tramite mnexec (prompt interattivo Q/N)
├── test_arp.py                # Script Scapy per forgiare frame gratuitous/reply ARP malevoli da m1
├── stats.log                  # Log degli allarmi di sicurezza generati dal controller
├── results/                   # Cartella contenente i report generati dalle prove
│   ├── arp_attack.txt         # Log di esecuzione dell'attacco ARP da m1
│   ├── h2_iperf.txt           # Statistiche iperf3 SCADA in scenario con QoS
│   ├── h3_iperf.txt           # Statistiche iperf3 Bulk Transfer in scenario con QoS
│   ├── noqos_h2_iperf.txt     # Statistiche iperf3 SCADA in scenario SENZA QoS
│   ├── noqos_h3_iperf.txt     # Statistiche iperf3 Bulk Transfer in scenario SENZA QoS
│   ├── ovs_queue_stats.txt    # Contatori hardware delle code HTB su porta 3 di s1
│   └── ping_after_attack.txt  # Verifica del drop hardware (100% loss) per m1 isolato
└── README.md
```
---

## Requisiti e Dipendenze Software

Il testbench è validato su ambiente Linux (26.04 LTS):
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y mininet openvswitch-switch openvswitch-testcontroller iperf3 iproute2 python3-pip python3-scapy
sudo pip3 install ryu eventlet==0.30.2
sudo systemctl enable --now openvswitch-switch
```
---

## Istruzioni per l'Esecuzione dei Test

L'architettura separa la topologia di rete dallo script di test: i file `topo.py` e `topoNoQos.py` esportano i descrittori dei processi host in `/tmp/mininet_pids.env` lasciando aperta la CLI interattiva di Mininet.  
Lo script `test_script.py` si aggancia ai nodi attivi tramite `mnexec -a <PID>` ed esegue l'intera batteria di test in modo deterministico.

---

### Scenario A: Test della Rete (CON QoS HTB)
1. **Terminale 1 (Controller SDN con QoS):**
    ```bash
   ryu-manager controller.py
   ```

2. **Terminale 2 (Topologia con Code HTB):**
   ```bash
   sudo mn -c
   sudo python3 topo.py #(La CLI mininet> rimane attiva per consentire comandi diagnostici quali pingall)
    ```
3. **Terminale 3 (Suite di Test Unificata):**
   ```bash
   sudo python3 test_script.py #Al prompt: digitare Q e premere Invio.
    ```
4. **Ispezione dei Risultati Generati:**
   ```bash
   cat results/h2_iperf.txt          # Telemetria SCADA protetta (0.0% loss, jitter < 1.2 ms)
   cat results/h3_iperf.txt          # Bulk Transfer saturante (loss ~33%)
   cat results/arp_attack.txt        # Trigger dell'attacco ARP da parte di m1
   cat results/ping_after_attack.txt # Verifica 100% packet loss per l'attaccante isolato
   cat results/ovs_queue_stats.txt   # Dump contatori pacchetti code 0 e 1 su s1-eth3
    ```
---

### Scenario B: Test del Benchmark di Controllo (SENZA QoS)

1. **Terminale 1 (Controller Neutro di Baseline):**
   ```bash
   ryu-manager controllerSenzaQoS.py
   ```

2. **Terminale 2 (Topologia Speculare No-QoS):**
    ```bash
   sudo mn -c
   sudo python3 topoNoQos.py
    ```
3. **Terminale 3 (Suite di Test Unificata):**
   ```bash
   sudo python3 test_script.py #Al prompt: digitare N (o premere direttamente Invio).
    ```
4. **Ispezione dei Risultati Generati:**
   ```bash
   cat results/noqos_h2_iperf.txt    # Telemetria SCADA degradata (loss ~51%, jitter elevato)
   cat results/noqos_h3_iperf.txt    # Bulk transfer concorrente non regolato
    ```
---

### Modalità Interattiva Manuale (Mininet CLI)

È sempre possibile eseguire manualmente ogni singolo passaggio dal Terminale 2 (mininet>):

# 1. Verifica connettività globale
```bash
mininet> pingall
```
# 2. Avvio manuale dei server di test
```bash
mininet> h2 iperf3 -s -p 5201 -D
mininet> h3 iperf3 -s -p 5202 -D
```
# 3. Flussi UDP simultanei da h1
```bash
mininet> h1 iperf3 -c 10.0.2.2 -u -b 12M -p 5202 -t 15 &
mininet> h1 iperf3 -c 10.0.2.1 -u -b 2M -p 5201 -t 15
```
# 4. Iniezione attacco ARP da m1
```bash
mininet> m1 python3 test_arp.py
```
# 5. Verifica isolamento hardware dell'attaccante
```bash
mininet> m1 ping -c 3 10.0.1.1
```
---

## Procedura di Pulizia dell'Ambiente

In caso di riavvio delle prove o per azzerare lo stato dei namespace e delle interfacce OVS:
```bash
sudo mn -c
sudo killall -9 ryu-manager iperf3 2>/dev/null
rm -f /tmp/mininet_pids.env
```