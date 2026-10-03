#!/usr/bin/env python3
"""
Controller Ryu per topology slicing + service slicing con enforcement QoS.

Pipeline OpenFlow 1.3 a tre tabelle:

    Tabella 0  CLASSIFICAZIONE   solo switch di bordo: match sulla 5-tupla,
                                 scrive il DSCP, prosegue.
    Tabella 1  MAPPATURA QoS     tutti gli switch: match sul solo DSCP,
                                 seleziona la coda di uscita, prosegue.
    Tabella 2  INOLTRO           regole di percorso per slice di topologia;
                                 le ARP salgono al controller.

E' il modello DiffServ: la classificazione, costosa e per flusso, esiste
solo al bordo; il core applica un per-hop behavior guardando un solo campo.

Tutte le regole sono installate proattivamente alla connessione dello
switch. Il controller non apprende nulla e non usa mai OFPP_FLOOD: e' cio'
che rende innocuo il ciclo presente nella topologia.

Uso:  ryu-manager controller/slicing_controller.py
"""

import csv
import os
import time

import yaml
from ryu.base import app_manager
#contiene classi per gestire eventi OpenFlow. usiamo 4 oggetti che vediamo
#nei decoratori, come parametro ev. dentro ev c'è ev.msg, cioè il messaggio.
#dentro msg c'è msg.datapath, cioè l'oggetto che rappresenta lo switch che manda il mess.
from ryu.controller import ofp_event 
#set_ev_cls è il decoratore che registra un metodo come gestore di un evento.
#CONFIG E MAIN sono due stati della connessione. 
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
#hub serve per la concorrenza. usiamo un green thread per il monitoring.
from ryu.lib import hub
#PACKET:gestione pacchetti. arp ed ethernet per i relativi protocolli.
#si può estrarre intestazione del protocollo, così come aggiungerla.
from ryu.lib.packet import arp, ethernet, packet
#ofproto_v1_3 contiene costanti e classi per OpenFlow 1.3.
from ryu.ofproto import ofproto_v1_3

POLICY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "policy", "slices.yaml")

TBL_CLASSIFY, TBL_QOS, TBL_FORWARD = 0, 1, 2
#mi dice il tipo di protocollo incapsulato nel payload.
ETH_IP, ETH_ARP = 0x0800, 0x0806
#valori del campo protocol IPV4 che indica quale protocollo trasporto è usato.
PROTO = {"tcp": 6, "udp": 17}

# Scenario di confronto: si attiva con  SLICING_BASELINE=1 ryu-manager ...
# e va usato insieme a  topo_slicing.py.  In baseline restano il
# topology slicing e l'ARP responder, ma marcatura e code HTB non vengono usate: tutto
# il traffico condivide una singola FIFO. E' il termine di paragone che
# rende misurabile l'effetto del service slicing.
BASELINE = os.environ.get("SLICING_BASELINE") == "1"


class SlicingController(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SlicingController, self).__init__(*args, **kwargs)
        with open(POLICY) as fh:
            self.policy = yaml.safe_load(fh)

        self.hosts = self.policy["hosts"]
        self.ports = self.policy["ports"]
        self.topo_slices = self.policy["topology_slices"]
        self.svc_slices = self.policy["service_slices"]

        # Switch di bordo: quelli che hanno almeno un host attaccato.
        self.edge = {h["switch"] for h in self.hosts.values()}

        # Indici di comodo: IP -> host e mac, e poi host -> nome della sua slice.
        self.by_ip = {h["ip"]: (name, h["mac"])
                      for name, h in self.hosts.items()}
        #slice_of = {'h1': 'upper', 'h2': 'lower', 'h3': 'upper', 'h4': 'lower'}
        self.slice_of = {ep: sl["name"]
                         for sl in self.topo_slices for ep in sl["endpoints"]}

        self.datapaths = {}
        self.prev_bytes = {}          # (dpid, port, queue) -> byte precedenti
        self._init_csv()
        #flusso di esecuzione parallelo gestito da ryu.
        hub.spawn(self._monitor_loop)

    # -- utilita' ----------------------------------------------------------
    #serve perché il controller riceve dp.id=1 e deve poter cercare self.ports["s1"]
    def _name(self, dpid):
        """Mininet assegna a sX il datapath id X."""
        return "s%d" % dpid

    #dp è lo switch, table la tabella, priorità della flow entry, il criterio di match.
    #actions è la lista di azioni da eseguire, goto è la tabella successiva.
    def _add_flow(self, dp, table, priority, match, actions=None, goto=None):
        """Installa una flow entry; senza azioni ne' goto la entry scarta."""
        #ofproto contiene costanti della versione di OF negoziata con lo switch.
        #parser contiene le classi per costruire i messaggi OF.
        ofp, parser = dp.ofproto, dp.ofproto_parser
        #lista delle istruzioni che compongono la regola (oggetti OFPInstructionActions). flow entry: istruzioni.
        #alcune istruzioni contengono azioni.
        inst = []
        if actions:
            #OFPInstructionActions: oggetto che contiene azioni. si istanzia con il tipo di istruzione (ad es apply) e la lista di azioni. (ad es setField) 
            inst.append(parser.OFPInstructionActions(
                ofp.OFPIT_APPLY_ACTIONS, actions))
        if goto is not None:
            #OFPInstructionGotoTable: oggetto istruzione che contiene la tabella successiva.
            inst.append(parser.OFPInstructionGotoTable(goto))
        #OFPFlowMod: oggetto messaggio che contiene la flow entry da installare.
        #di default ha comando OFPFC_ADD, quindi aggiunge la flow entry. gli serve dp per sapere la versione di OF in cui scrivere il messaggio.
        #send_msg: asincrono, serializza il messaggio e lo accoda (FIFO) verso lo switch. la coda è in ryu.
        dp.send_msg(parser.OFPFlowMod(datapath=dp, table_id=table,
                                      priority=priority, match=match,
                                      instructions=inst))
        #con send_msg non c'è conferma di ricezione. si verifica con dump-flows sullo switch
   
    # -- installazione della pipeline --------------------------------------

    #gestore dell'evento SwitchFeatures, inviato dallo switch(in risposta alla richiesta di features) quando si connette al controller.
    #il messaggio contiene le caratteristiche dello switch, tra cui il datapath id, numero tabelle.
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _on_switch_up(self, ev):
        dp = ev.msg.datapath # datapath rappresenta lo switch che si è connesso. contiene id, versione OF, numero tabelle, porte.
        name = self._name(dp.id)
        self.datapaths[dp.id] = dp # registro lo switch nel dizionario, serve al monitor.
        #limite: se lo switch si scollega, non viene rimosso dal dizionario. si potrebbe gestire l'evento OFPSwitchStateChange.

        self._install_classify(dp, name)
        self._install_qos(dp) # non serve name perché la tabella è uguale per tutti.
        self._install_forward(dp, name)
        self.logger.info("pipeline installata su %s (%s, scenario %s)", name,
                         "bordo" if name in self.edge else "core",
                         "BASELINE" if BASELINE else "slicing+QoS")

    @set_ev_cls(ofp_event.EventOFPErrorMsg, MAIN_DISPATCHER)
    def _on_error(self, ev):
        """Segnala le flow entry rifiutate dallo switch.

        Senza questo gestore un OFPFlowMod malformato viene scartato in
        silenzio: la rete sembra installata ma non inoltra, e la causa e'
        invisibile. Il codice di errore piu' frequente qui e' un campo di
        match non supportato dalla tabella.
        """
        self.logger.error("errore OpenFlow: type=%d code=%d",
                          ev.msg.type, ev.msg.code)

    def _install_classify(self, dp, name):
        """Tabella 0: marcatura DSCP, solo sugli switch di bordo."""
        #prendiamo il parser della versione di OF negoziata con lo switch.
        parser = dp.ofproto_parser

        if name in self.edge and not BASELINE:
            for sl in self.svc_slices:
                m = sl["match"]
                if not m:# best effort: prossima iterazione o escidal ciclo
                    continue
                proto = PROTO[m["ip_proto"]] #valore num identificativo del protocollo
                kwargs = {"eth_type": ETH_IP, "ip_proto": proto}
                kwargs["tcp_dst" if proto == 6 else "udp_dst"] = m["dst_port"]
                self._add_flow(
                    dp, TBL_CLASSIFY, sl["priority"],
                    parser.OFPMatch(**kwargs),#equivale a OFPMatch(eth_type=2048, ip_proto=17, udp_dst=5001)
                    actions=[parser.OFPActionSetField(ip_dscp=sl["dscp"])],
                    goto=TBL_QOS)

            # Azzera il DSCP di tutto il traffico IP non classificato.
            # Senza questa regola un host potrebbe marcarsi da solo i
            # pacchetti e accedere a una classe cui non ha diritto: la
            # marcatura deve essere imposta al bordo, non ereditata dalla
            # sorgente. E' il confine di fiducia del modello DiffServ.
            self._add_flow(
                dp, TBL_CLASSIFY, 1,
                parser.OFPMatch(eth_type=ETH_IP),
                actions=[parser.OFPActionSetField(ip_dscp=0)],
                goto=TBL_QOS)

        # Tutto il resto (e tutto il traffico sugli switch di core)
        # priorità 0, OFPmatch() matcha tutti. se non c'è un'altra regola, il pacchetto va alla prox tabella.
        self._add_flow(dp, TBL_CLASSIFY, 0, parser.OFPMatch(), goto=TBL_QOS)

    def _install_qos(self, dp):
        """Tabella 1: dal DSCP alla coda di uscita. Su tutti gli switch."""
        parser = dp.ofproto_parser

        if BASELINE:
            # Nessuna coda configurata sulle porte: la tabella diventa un
            # semplice passaggio.
            self._add_flow(dp, TBL_QOS, 0, parser.OFPMatch(),
                           goto=TBL_FORWARD)
            return
        #per ogni slice installo una regola che matcha sul DSCP e setta la coda su cui va il pacchetto.
        #l'azione agisce sul contesto, una struttura nello switch, NON scrive sul
        #pacchetto. 
        for sl in self.svc_slices:
            if sl["dscp"] == 0:  # e' il caso di default
                continue
            self._add_flow(
                dp, TBL_QOS, 100,
                parser.OFPMatch(eth_type=ETH_IP, ip_dscp=sl["dscp"]),
                actions=[parser.OFPActionSetQueue(sl["queue_id"])],    #setQueue: nel contesto dello switch.
                goto=TBL_FORWARD)

        # Default: coda best effort. Cattura anche le ARP, che non hanno DSCP. prio 0.
        be = next(s for s in self.svc_slices if s["dscp"] == 0)
        self._add_flow(dp, TBL_QOS, 0, parser.OFPMatch(),
                       actions=[parser.OFPActionSetQueue(be["queue_id"])],  
                       goto=TBL_FORWARD)

    def _install_forward(self, dp, name):
        """Tabella 2: percorsi fissi per slice di topologia.

        Per ogni slice si installano le regole nelle due direzioni. Il match
        su coppia (eth_src, eth_dst) fa si' che il traffico tra host di slice
        diverse non trovi alcuna regola e venga scartato dal table-miss:
        e' l'isolamento del topology slicing.
        """
        parser = dp.ofproto_parser
        #per ogni slice di topologia.
        for sl in self.topo_slices:
            a, b = sl["endpoints"]
            path = sl["path"]
            #se lo switch non appartiene al path, salto l'iterazione.
            if name not in path:
                continue
            #percorso e percorso inverso.
            for src, dst, hops in ((a, b, path), (b, a, path[::-1])):
                #hops è la lista di switch lungo path.
                i = hops.index(name)
                # ultimo hop: si esce verso l'host, altrimenti verso il
                # prossimo switch del percorso
                nxt= dst if i==(len(hops)-1) else hops[i+1]
                #cerco la porta versio il nexthop.
                out= self.ports[name][nxt]
                #regola che indica su quale port INOLTRARE il pacchetto.
                self._add_flow(
                    dp, TBL_FORWARD, 10,
                    parser.OFPMatch(eth_src=self.hosts[src]["mac"],
                                    eth_dst=self.hosts[dst]["mac"]),
                    actions=[parser.OFPActionOutput(out)])

        # Le ARP salgono al controller, che risponde al posto degli host.
        # Nessun flooding: il ciclo non è un problema. 
        #l'azione manda il pacchetto al controller per intero.
        if name in self.edge:
            self._add_flow(
                dp, TBL_FORWARD, 20, parser.OFPMatch(eth_type=ETH_ARP),
                actions=[parser.OFPActionOutput(
                    dp.ofproto.OFPP_CONTROLLER,
                    dp.ofproto.OFPCML_NO_BUFFER)])

        # Table-miss senza istruzioni: scarta.
        self._add_flow(dp, TBL_FORWARD, 0, parser.OFPMatch())

    # -- ARP responder -----------------------------------------------------
    #PacketIn è il pacchetto che lo switch manda al controller. Nel nostro caso
    #si tratta sempre di pacchetti ARP.
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _on_packet_in(self, ev):
        """Risponde alle richieste ARP solo dentro la stessa slice.

        Se due host appartengono a slice di topologia diverse, la richiesta
        viene ignorata: l'isolamento vale anche a livello di risoluzione
        degli indirizzi, non solo di inoltro.
        """
        msg = ev.msg
        #divide i byte del messaggio negli strati protocollo diversi: si può fare perché 
        #nella funzione che manda il messaggio facciamo NO_BUFFER.
        pkt = packet.Packet(msg.data)
        #cerca l'intestazione ARP nel pacchetto. se non c'è, ritorna None. specifico arp
        #perché nel pachetto trovo più strati (ethernet,arp etc).
        req = pkt.get_protocol(arp.arp)
        #se non c'è un pacchetto ARP o è una reply, esco (scarto).
        if req is None or req.opcode != arp.ARP_REQUEST:
            return
        #il by_ip.get mi da la coppia (nome, mac).
        target = self.by_ip.get(req.dst_ip)
        source = self.by_ip.get(req.src_ip)
        if not target or not source:
            return

        tgt_name, tgt_mac = target
        src_name, _ = source
        if self.slice_of.get(tgt_name) != self.slice_of.get(src_name):
            self.logger.info("ARP %s -> %s bloccata: slice diverse",
                             src_name, tgt_name)
            return

        self._send_arp_reply(msg, req, tgt_mac)

    def _send_arp_reply(self, msg, req, tgt_mac):
        dp = msg.datapath
        parser = dp.ofproto_parser
        #match contiene i campi che lo switch ha allegato al packet_in.
        in_port = msg.match["in_port"]

        reply = packet.Packet()
        reply.add_protocol(ethernet.ethernet(
            ethertype=ETH_ARP, dst=req.src_mac, src=tgt_mac))
        reply.add_protocol(arp.arp(
            opcode=arp.ARP_REPLY, src_mac=tgt_mac, src_ip=req.dst_ip,
            dst_mac=req.src_mac, dst_ip=req.src_ip))
        #con serialize, usa tutti i campi di reply per costruire i byte da inviare
        #nel campo reply.data.
        reply.serialize()

        #OFPPacketOut il controller manda un pacchetto allo switch. 
        #Con NoBuffer, viene inviato il pacchetto intero, altrimenti staremmo dicendo allo swtich
        #che già ha il pacchetto nella propria memoria.
        dp.send_msg(parser.OFPPacketOut(
            datapath=dp, buffer_id=dp.ofproto.OFP_NO_BUFFER,
            in_port=dp.ofproto.OFPP_CONTROLLER, #non puoi fare inport=in_port perché OF lo scarterebbe.
            actions=[parser.OFPActionOutput(in_port)], data=reply.data))
        #avendo specificato actions, il pacchetto viene mandato in quell porta,
        #senza passare per le tabelle.
    
    # -- monitoraggio ------------------------------------------------------

    def _init_csv(self):
        self.csv_path = self.policy["monitor"]["csv"]
        os.makedirs(os.path.dirname(self.csv_path) or ".", exist_ok=True)
        with open(self.csv_path, "w", newline="") as fh:
            csv.writer(fh).writerow(
                ["t", "switch", "port", "queue", "mbit_s"])

    def _monitor_loop(self):
        """Interroga periodicamente le statistiche per coda."""
        interval = self.policy["monitor"]["interval_s"]
        parser_req = None
        while True:
            hub.sleep(interval)
            for dp in list(self.datapaths.values()):
                parser_req = dp.ofproto_parser.OFPQueueStatsRequest(
                    dp, 0, dp.ofproto.OFPP_ANY, dp.ofproto.OFPQ_ALL)
                dp.send_msg(parser_req)

    @set_ev_cls(ofp_event.EventOFPQueueStatsReply, MAIN_DISPATCHER)
    def _on_queue_stats(self, ev):
        """Converte i contatori cumulativi in throughput sull'intervallo."""
        dpid = ev.msg.datapath.id
        interval = self.policy["monitor"]["interval_s"]
        now = time.time()
        rows = []

        for st in ev.msg.body:
            key = (dpid, st.port_no, st.queue_id)
            prev = self.prev_bytes.get(key)
            self.prev_bytes[key] = st.tx_bytes
            if prev is None:                   # prima lettura: nessun delta
                continue
            mbit_s = (st.tx_bytes - prev) * 8 / interval / 1e6
            rows.append([round(now, 1), self._name(dpid), st.port_no,
                         st.queue_id, round(mbit_s, 3)])

        if rows:
            with open(self.csv_path, "a", newline="") as fh:
                csv.writer(fh).writerows(rows)
