from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls
)
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib import hub

import time


class DynamicMitigationController(app_manager.RyuApp):

    # utilizza OpenFlow 1.3
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # definisce la soglia utilizzata per rilevare traffico anomalo
    THRESHOLD_MBPS = 12.0

    # definisce il numero di campioni anomali necessari per il blocco
    ATTACK_SAMPLES = 3

    # definisce il numero di campioni normali necessari per lo sblocco
    RECOVERY_SAMPLES = 3

    # associa le porte edge agli host monitorati
    EDGE_PORTS = {
        (1, 1): "h1",
        (2, 1): "h2"
    }

    def __init__(self, *args, **kwargs):
        super(DynamicMitigationController, self).__init__(*args, **kwargs)

        # memorizza le porte associate ai mac
        self.mac_to_port = {}

        # memorizza gli switch collegati al controller
        self.datapaths = {}

        # memorizza le statistiche del campione precedente
        self.previous_stats = {}

        # conta i campioni consecutivi sopra soglia
        self.high_samples = {}

        # conta i campioni consecutivi sotto soglia
        self.low_samples = {}

        # memorizza le porte già bloccate
        self.blocked_ports = set()

        # avvia il monitoraggio periodico
        self.monitor_thread = hub.spawn(self.monitor)

    def add_flow(self, datapath, priority, match, actions):

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # trasforma le azioni in istruzioni OpenFlow
        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        # crea la regola da installare nello switch
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=instructions
        )

        # invia la regola allo switch
        datapath.send_msg(mod)

    def add_drop_flow(self, datapath, in_port):

        parser = datapath.ofproto_parser

        # crea un match sulla porta da bloccare
        match = parser.OFPMatch(
            in_port=in_port
        )

        # installa una regola di drop con priorità elevata
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=100,
            match=match,
            instructions=[]
        )

        datapath.send_msg(mod)

    def remove_drop_flow(self, datapath, in_port):

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # seleziona la regola di drop associata alla porta
        match = parser.OFPMatch(
            in_port=in_port
        )

        # rimuove esclusivamente la regola di drop
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE_STRICT,
            priority=100,
            match=match,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY
        )

        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):

        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # crea un match valido per tutti i pacchetti
        match = parser.OFPMatch()

        # invia al controller i pacchetti senza una regola specifica
        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER
            )
        ]

        # installa la regola table-miss
        self.add_flow(
            datapath,
            priority=0,
            match=match,
            actions=actions
        )

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):

        msg = ev.msg
        datapath = msg.datapath

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # recupera l identificativo dello switch e la porta di ingresso
        dpid = datapath.id
        in_port = msg.match["in_port"]

        # analizza il pacchetto ricevuto
        pkt = packet.Packet(msg.data)

        # estrae l header ethernet
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        # ignora i pacchetti LLDP
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src = eth.src
        dst = eth.dst

        # crea la tabella associata allo switch se non esiste
        self.mac_to_port.setdefault(dpid, {})

        # associa il mac sorgente alla porta di ingresso
        self.mac_to_port[dpid][src] = in_port

        # utilizza la porta nota se la destinazione è già stata appresa
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]

        else:

            # esegue il flooding se la destinazione non è conosciuta
            out_port = ofproto.OFPP_FLOOD

        # crea l azione di output
        actions = [
            parser.OFPActionOutput(out_port)
        ]

        # installa la regola se la destinazione è conosciuta
        if out_port != ofproto.OFPP_FLOOD:

            match = parser.OFPMatch(
                in_port=in_port,
                eth_dst=dst
            )

            self.add_flow(
                datapath,
                priority=1,
                match=match,
                actions=actions
            )

        data = None

        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        # inoltra il pacchetto che ha generato il PacketIn
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )

        datapath.send_msg(out)

    # mantiene aggiornata la lista degli switch collegati
    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER]
    )
    def state_change_handler(self, ev):

        datapath = ev.datapath

        if ev.state == MAIN_DISPATCHER:

            # registra lo switch quando diventa attivo
            if datapath.id not in self.datapaths:
                self.datapaths[datapath.id] = datapath

        elif ev.state == DEAD_DISPATCHER:

            # rimuove lo switch quando si disconnette
            if datapath.id in self.datapaths:
                del self.datapaths[datapath.id]

    # richiede le statistiche ogni 3 secondi
    def monitor(self):

        while True:

            for datapath in self.datapaths.values():
                self.request_port_stats(datapath)

            hub.sleep(3)

    def request_port_stats(self, datapath):

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # crea la richiesta delle statistiche di tutte le porte
        request = parser.OFPPortStatsRequest(
            datapath,
            0,
            ofproto.OFPP_ANY
        )

        # invia la richiesta allo switch
        datapath.send_msg(request)

    # elabora le statistiche e decide se bloccare o sbloccare
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):

        datapath = ev.msg.datapath
        dpid = datapath.id

        # memorizza l istante della misura corrente
        now = time.time()

        for stat in ev.msg.body:

            port_no = stat.port_no

            # ignora le porte riservate di OpenFlow
            if port_no > 100:
                continue

            key = (dpid, port_no)

            # recupera il numero totale di byte ricevuti
            current_bytes = stat.rx_bytes

            # esegue il calcolo solo se esiste una misura precedente
            if key in self.previous_stats:

                previous_time, previous_bytes = self.previous_stats[key]

                # calcola la variazione di tempo e byte
                delta_time = now - previous_time
                delta_bytes = current_bytes - previous_bytes

                if delta_time > 0 and delta_bytes >= 0:

                    # calcola il throughput medio in Mbit/s
                    mbps = (
                        delta_bytes * 8
                        / delta_time
                        / 1_000_000
                    )

                    # visualizza il traffico rilevato sulla porta
                    if mbps > 0.01:

                        self.logger.info(
                            "s%s port %s rx %.3f Mbit/s",
                            dpid,
                            port_no,
                            mbps
                        )

                    # applica il controllo solo alle porte edge monitorate
                    if key in self.EDGE_PORTS:

                        host = self.EDGE_PORTS[key]

                        # gestisce il caso in cui la porta non è bloccata
                        if key not in self.blocked_ports:

                            if mbps >= self.THRESHOLD_MBPS:

                                # incrementa il numero di campioni sopra soglia
                                self.high_samples[key] = (
                                    self.high_samples.get(key, 0) + 1
                                )

                                self.logger.warning(
                                    "%s sopra soglia %s/%s %.3f Mbit/s",
                                    host,
                                    self.high_samples[key],
                                    self.ATTACK_SAMPLES,
                                    mbps
                                )

                                # attiva la mitigation dopo più superamenti
                                if (
                                    self.high_samples[key]
                                    >= self.ATTACK_SAMPLES
                                ):

                                    self.logger.warning(
                                        "ATTACK detected from %s",
                                        host
                                    )

                                    # installa la regola di drop
                                    self.add_drop_flow(
                                        datapath,
                                        port_no
                                    )

                                    # registra la porta come bloccata
                                    self.blocked_ports.add(key)

                                    # azzera i contatori
                                    self.high_samples[key] = 0
                                    self.low_samples[key] = 0

                                    self.logger.warning(
                                        "MITIGATION active %s blocked",
                                        host
                                    )

                            else:

                                # azzera il conteggio se il traffico torna normale
                                self.high_samples[key] = 0

                        # gestisce il caso in cui la porta è già bloccata
                        else:

                            if mbps < self.THRESHOLD_MBPS:

                                # incrementa i campioni consecutivi sotto soglia
                                self.low_samples[key] = (
                                    self.low_samples.get(key, 0) + 1
                                )

                                self.logger.info(
                                    "%s sotto soglia %s/%s %.3f Mbit/s",
                                    host,
                                    self.low_samples[key],
                                    self.RECOVERY_SAMPLES,
                                    mbps
                                )

                                # rimuove il blocco dopo più campioni normali
                                if (
                                    self.low_samples[key]
                                    >= self.RECOVERY_SAMPLES
                                ):

                                    self.remove_drop_flow(
                                        datapath,
                                        port_no
                                    )

                                    # rimuove la porta dall insieme delle porte bloccate
                                    self.blocked_ports.remove(key)

                                    # azzera i contatori
                                    self.low_samples[key] = 0
                                    self.high_samples[key] = 0

                                    self.logger.info(
                                        "RECOVERY %s unblocked",
                                        host
                                    )

                            else:

                                # azzera il recovery se il traffico torna sopra soglia
                                self.low_samples[key] = 0

            # salva il campione corrente per il calcolo successivo
            self.previous_stats[key] = (
                now,
                current_bytes
            )
