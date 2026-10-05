from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, arp
import time

class ARPSecurityApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ARPSecurityApp, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        
        # Binding Table Statica autorevole: (DPID, In_Port) -> (IP, MAC)
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
        
        # Regola Table-Miss di default (priority=0): invio al controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        
        # Invia tutti i pacchetti ARP al controller per ispezione DAI (priority=1000)
        match_arp = parser.OFPMatch(eth_type=0x0806)
        self.add_flow(datapath, 1000, match_arp, actions)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None, idle_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst, idle_timeout=idle_timeout)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst,
                                    idle_timeout=idle_timeout)
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

        # Controllo di sicurezza DAI su pacchetti ARP
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            trusted = self.trusted_arp.get((dpid, in_port))
            if trusted:
                trusted_ip, trusted_mac = trusted
                if arp_pkt.src_ip != trusted_ip or arp_pkt.src_mac != trusted_mac:
                    alert_msg = "[{0}] SECURITY ALERT: ARP Spoofing Detected! DPID: {1}, Port: {2}, Expected: {3}/{4}, Got: {5}/{6}".format(
                                 time.ctime(), dpid, in_port, trusted_ip, trusted_mac, arp_pkt.src_ip, arp_pkt.src_mac)
                    self.logger.warning(alert_msg)
                    
                    # Blocco immediato del traffico malevolo con regola di drop
                    match_drop = parser.OFPMatch(in_port=in_port, eth_src=eth.src)
                    self.add_flow(datapath, priority=100, match=match_drop, actions=[], idle_timeout=60)
                    return

        # Apprendimento L2 standard
        src = eth.src
        dst = eth.dst
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Installazione della regola L2 per evitare Packet-In successivi
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
            self.add_flow(datapath, priority=10, match=match, actions=actions, buffer_id=msg.buffer_id, idle_timeout=30)
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                return

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)