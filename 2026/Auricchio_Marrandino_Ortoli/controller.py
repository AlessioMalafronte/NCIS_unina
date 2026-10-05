from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4, udp
from ryu.lib import hub
import time

class QoSARPSecurityApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(QoSARPSecurityApp, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.monitor_thread = hub.spawn(self._monitor)
        
        self.trusted_arp = {
            (1, 1): ('10.0.1.1', '00:00:00:00:01:01'), 
            (1, 2): ('10.0.1.2', '00:00:00:00:01:02'), 
            (1, 3): ('10.0.1.254', '00:00:00:00:0a:01'),
            (2, 1): ('10.0.2.1', '00:00:00:00:02:01'), 
            (2, 2): ('10.0.2.2', '00:00:00:00:02:02'), 
            (2, 3): ('10.0.2.254', '00:00:00:00:0b:01')  
        }

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        self.datapaths[datapath.id] = datapath
        
        # Table-miss flow
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        
        # Invia sempre tutti i pacchetti ARP al controller per ispezione
        match_arp = parser.OFPMatch(eth_type=0x0806)
        self.add_flow(datapath, 1000, match_arp, actions)

        # --- REGOLE QoS PROATTIVE SU S1 ---
        # Installiamo subito le regole per il traffico UDP in modo che 
        # non vengano sovrascritte dalle normali regole di L2 learning create dal ping.
        if datapath.id == 1:
            # Traffico Prioritario (verso h2 UDP) -> Coda 0, inoltro verso router (Porta 3)
            m_q0 = parser.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_dst="10.0.2.1")
            a_q0 = [parser.OFPActionSetQueue(0), parser.OFPActionOutput(3)]
            self.add_flow(datapath, 50, m_q0, a_q0)
            
            # Traffico Best Effort (verso h3 UDP) -> Coda 1, inoltro verso router (Porta 3)
            m_q1 = parser.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_dst="10.0.2.2")
            a_q1 = [parser.OFPActionSetQueue(1), parser.OFPActionOutput(3)]
            self.add_flow(datapath, 50, m_q1, a_q1)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority, match=match, instructions=inst)

        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            trusted = self.trusted_arp.get((dpid, in_port))
            if trusted:
                trusted_ip, trusted_mac = trusted
                if arp_pkt.src_ip != trusted_ip or arp_pkt.src_mac != trusted_mac:
                    alert_msg = "[{0}] SECURITY ALERT: ARP Spoofing Detected! DPID: {1}, Port: {2}, Expected: {3}/{4}, Got: {5}/{6}\n".format(
                                 time.ctime(), dpid, in_port, trusted_ip, trusted_mac, arp_pkt.src_ip, arp_pkt.src_mac)
                    self.logger.warning(alert_msg)
                    with open('stats.log', 'a') as f:
                        f.write(alert_msg)
                    match_drop = parser.OFPMatch(in_port=in_port, eth_src=eth.src)
                    self.add_flow(datapath, 100, match_drop, [])
                    return

        src = eth.src
        dst = eth.dst
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
            self.add_flow(datapath, 10, match, actions, msg.buffer_id)
            
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                if dp.id == 1: 
                    self._request_stats(dp)
            hub.sleep(10)

    def _request_stats(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        req = parser.OFPQueueStatsRequest(datapath, 0, ofproto.OFPP_ANY, ofproto.OFPQ_ALL)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPQueueStatsReply, MAIN_DISPATCHER)
    def _queue_stats_reply_handler(self, ev):
        msg = ev.msg
        dpid = msg.datapath.id
        
        log_out = "[{0}] --- QoS Stats for Switch {1} ---\n".format(time.ctime(), dpid)
        for stat in msg.body:
            log_out += "  Port: {0}, Queue: {1}, Tx Bytes: {2}, Tx Packets: {3}\n".format(
                        stat.port_no, stat.queue_id, stat.tx_bytes, stat.tx_packets)
        
        self.logger.info(log_out.strip())
        with open('stats.log', 'a') as f:
            f.write(log_out)
