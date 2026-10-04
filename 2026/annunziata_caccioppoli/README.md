# QoS-Aware Network Slicing in SDN

Network slicing in un ambiente SDN emulato con Mininet e controller Ryu
(OpenFlow 1.3). Il progetto combina due dimensioni di isolamento ortogonali
tra loro e aggiunge un motore di policy adattivo con API di controllo.

- **Topology slicing**: isolamento per tenant. Ogni tenant comunica solo
  attraverso un percorso di rete predefinito; il traffico cross-tenant viene
  scartato.
- **Service slicing**: QoS intra-tenant. Sul percorso premium video, voce e
  best-effort competono per la banda tramite code HTB con banda minima
  garantita e prestito dinamico della capacita inutilizzata.

## Autori

- Maria Pia Annunziata - M63001844
- Luca Caccioppoli 

## Requisiti

- Ubuntu 20.04 / WSL2
- Mininet e Open vSwitch (`sudo apt install mininet openvswitch-switch`)
- Python 3.9 con Ryu in virtualenv
- `tcpdump`, `iperf`

Setup della venv per Ryu:

    python3.9 -m venv ~/ryu-venv
    source ~/ryu-venv/bin/activate
    pip install --upgrade "pip<24"
    pip install "setuptools<58" wheel
    pip install ryu eventlet==0.30.2

## Struttura del repository

    topology.py                 definizione della rete Mininet e configurazione code OVS
    controller.py               controller Ryu: slicing, monitor, policy engine, API REST
    relazione_QoS_SDN.docx      relazione PDF e figure

## Topologia

Due campus (s1, s2), ciascuno con tre host, collegati da due percorsi core
paralleli:

- Percorso premium (s3): 10 Mbps, tenant real-time (video + voce)
- Percorso standard (s4): 2 Mbps, tenant dati

| Host | IP        | Tenant   | Servizio |
|------|-----------|----------|----------|
| h1   | 10.0.0.1  | premium  | video    |
| h2   | 10.0.0.2  | premium  | voce     |
| h3   | 10.0.0.3  | standard | dati     |
| h4   | 10.0.0.4  | premium  | video    |
| h5   | 10.0.0.5  | premium  | voce     |
| h6   | 10.0.0.6  | standard | dati     |

La topologia contiene un ciclo (s1-s3-s2-s4). Il broadcast storm e evitato
con ARP statico, quindi il controller non fa mai flooding sui due percorsi.

## Esecuzione

Terminale 1, controller:

    source ~/ryu-venv/bin/activate
    ryu-manager controller.py

Terminale 2, topologia:

    sudo mn -c
    sudo python3 topology.py

Pulizia delle code:

    sudo ovs-vsctl --all destroy qos
    sudo ovs-vsctl --all destroy queue

## Test

**1. Isolamento topologico**

    mininet> pingall

Atteso: 14/30 ricevuti. Solo comunicazioni intra-tenant.

**2. Verifica del percorso**

    mininet> h1 ping -c 3 h4
    mininet> sudo ovs-ofctl -O OpenFlow13 dump-flows s3

I contatori delle regole su s3 confermano che il traffico premium passa da s3.

**3. Code OVS installate**

    sudo ovs-vsctl list qos
    sudo ovs-ofctl -O OpenFlow13 dump-flows s3

Le regole premium mostrano `set_queue:1` (video) e `set_queue:2` (voce).

**4. QoS sotto congestione**

Su h4/h5 avviare i server iperf (UDP 9999, UDP 5060, TCP), su h1/h2 i client:

    h1: iperf -c 10.0.0.4 -u -p 9999 -b 6M -t 60   (video)
    h1: iperf -c 10.0.0.4 -t 60                     (TCP greedy)
    h2: iperf -c 10.0.0.5 -u -p 5060 -b 2M -t 60    (voce)

Atteso: video ~6 Mbps, voce ~2 Mbps, TCP relegato alla banda residua.

**5. Borrowing dinamico**

Interrompendo il flusso video, il TCP best-effort sale fino a ~9 Mbps;
riavviando il video, torna a cedere la banda garantita.

**6. Policy engine e API**

    curl http://127.0.0.1:8080/qos/stats
    curl -X POST http://127.0.0.1:8080/qos/sla -d '{"video_min": 9000000}'

## Architettura del controller

- **Monitor**: thread periodico, richiede le statistiche delle code e mantiene una serie storica scorrevole del throughput.
- **Policy engine**: valuta il rispetto degli SLA su media mobile; richiede persistenza della violazione (piu intervalli) per evitare falsi positivi.
- **Enforcer**: unico componente che scrive sugli switch (flow rule, code).
- **API REST**: espone le metriche e permette override esterni delle policy (base per un'eventuale dashboard futura, non inclusa in questa consegna).