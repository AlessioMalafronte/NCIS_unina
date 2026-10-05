# Network Slicing con Ryu e Mininet

Implementazione del network slicing in una rete SDN emulata con **Mininet** e controllata da **Ryu** (OpenFlow 1.3).

- **Topology slicing**: H1 comunica solo con H3 attraverso la slice superiore, H2 solo con H4 attraverso la slice inferiore.
- **Service slicing**: il traffico video (UDP, porta di destinazione 9999) usa la slice superiore, tutto il resto quella inferiore. Tutti gli host comunicano tra loro.

## Topologia

```
H1 ─┐                              ┌─ H3
    S1 ── S2 (10 Mbit/s, 15 ms) ── S4
H2 ─┘ └── S3 (1 Mbit/s, 30 ms) ──┘  └─ H4
```

- **Slice superiore**: S1 – S2 – S4
- **Slice inferiore**: S1 – S3 – S4
- Host Hn: MAC `00:00:00:00:00:0n`, IP `10.0.0.n`

| File | Contenuto |
|---|---|
| `topology.py` | Topologia Mininet (comune ai due esercizi) |
| `topology_slicing.py` | Controller Ryu per il topology slicing |
| `topology_service.py` | Controller Ryu per il service slicing |
| `requirements.txt`| Dipendenze Python |

## Requisiti

- Linux con **Mininet 2.3** e **Open vSwitch**, installati a livello di sistema
- **iperf** (versione 2) e **tcpdump** per i test
- **Python 3.9** in un ambiente virtuale per Ryu, che non è compatibile con le versioni più recenti

```bash
# Pacchetti di sistema
sudo apt install mininet openvswitch-switch iperf tcpdump

# Ambiente virtuale per Ryu
uv venv --python 3.9 --seed .venv      # oppure: python3.9 -m venv .venv
source .venv/bin/activate              # attivazione venv
```

`install.sh` fissa le versioni necessarie a compilare Ryu 4.34:

```bash
pip install setuptools==67.6.1 wheel==0.45.1 pbr eventlet==0.30.2
pip install --no-build-isolation --no-cache-dir ryu
```

## Avvio

Servono due terminali. Avviare **prima il controller, poi la rete**.

```bash
# Terminale 1 — controller (venv attivo)
ryu-manager topology_slicing.py     # esercizio 1
ryu-manager topology_service.py     # esercizio 2

# Terminale 2 — rete
sudo mn -c
sudo python3 topology.py
```

Per chiudere: `exit` nella CLI di Mininet, `Ctrl+C` su Ryu. Dopo un'interruzione anomala eseguire `sudo mn -c`.

## Test

I comandi senza `sudo` vanno eseguiti nella CLI di Mininet; `tcpdump` in un terzo terminale.

### Test Topology slicing

| Test | Comando | Risultato atteso |
|---|---|---|
| Isolamento | `pingall` | 66% dropped: rispondono solo H1↔H3 e H2↔H4 |
| Regole | `dpctl dump-flows -O OpenFlow13` | S2 ha regole solo per H1/H3, S3 solo per H2/H4 |
| Slice superiore | `sudo tcpdump -i s2-eth1 -n icmp` + `h1 ping -c3 h3` | Solo traffico 10.0.0.1 ↔ 10.0.0.3 |
| Slice inferiore | `sudo tcpdump -i s3-eth1 -n icmp` + `h2 ping -c3 h4` | Solo traffico 10.0.0.2 ↔ 10.0.0.4 |
| Unicast bloccato | `h1 arp -s 10.0.0.4 00:00:00:00:00:04` + `h1 ping -c2 h4` | Ping fallito, regola di drop su S1 |

### Test Service slicing

| Test | Comando | Risultato atteso |
|---|---|---|
| Connettività | `pingall` | 0% dropped |
| Banda video | `h3 iperf -s -u -p 9999 &` + `h1 iperf -c 10.0.0.3 -u -p 9999 -b 10M -t 5` | ≈ 10 Mbit/s nel Server Report |
| Banda non video | `h3 iperf -s -u -p 5001 &` + `h1 iperf -c 10.0.0.3 -u -p 5001 -b 10M -t 5` | ≈ 1 Mbit/s, perdita ≈ 90% |
| Percorsi | `sudo tcpdump -i s2-eth1 -n udp` / `-i s3-eth1` durante iPerf | Porta 9999 solo su S2, porta 5001 solo su S3 |
| Regole | `dpctl dump-flows -O OpenFlow13` | Su S1, regole a priorità 20 con `udp_dst=9999` accanto a quelle a priorità 10 |
