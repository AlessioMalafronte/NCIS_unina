from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import set_ev_cls, CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, udp, in_proto


BROADCAST = 'ff:ff:ff:ff:ff:ff'

VIDEO_UDP_PORT = 9999

EDGE_SWITCHES = {
    1: {'host_ports': [1, 2], 'video': 3, 'other': 4},
    4: {'host_ports': [3, 4], 'video': 1, 'other': 2},
}

CORE_SWITCHES = [2, 3]
CORE_TRANSIT = {1: 2, 2: 1}

VIDEO_PRIORITY = 20
FORWARD_PRIORITY = 10
DROP_PRIORITY = 5


class ServiceSlicing(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        actions = [
            parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, parser.OFPMatch(), actions)

        match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IPV6)
        self.add_flow(datapath, DROP_PRIORITY, match, [])

        self.logger.info(f'[SWITCH] Switch s{datapath.id} connected')

    def add_flow(self, datapath, priority, match, actions, idle=0, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(
                datapath=datapath, buffer_id=buffer_id, priority=priority,
                match=match, instructions=inst, idle_timeout=idle
            )
        else:
            mod = parser.OFPFlowMod(
                datapath=datapath, priority=priority,
                match=match, instructions=inst, idle_timeout=idle
            )
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        dpid = datapath.id
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src = eth.src
        dst = eth.dst
        is_video = self.is_video_flow(
            pkt.get_protocol(ipv4.ipv4), pkt.get_protocol(udp.udp))
        
        if dpid in CORE_SWITCHES:
            out_port = CORE_TRANSIT.get(in_port)
            if out_port is None:
                return
            actions = [parser.OFPActionOutput(out_port)]
            self.add_flow(
                datapath, FORWARD_PRIORITY, parser.OFPMatch(in_port=in_port), actions)
            self.send_packet_out(datapath, msg, actions)
            return

        cfg = EDGE_SWITCHES.get(dpid)
        if cfg is None:
            return

        if in_port in cfg['host_ports']:
            self.update_mac_to_port_table(dpid, src, in_port)

        if dst == BROADCAST:
            self.handle_broadcast(datapath, msg, cfg, in_port)
            return

        out_port = self.get_output_port(dpid, cfg, dst, in_port, is_video)
        if out_port is None:
            self.logger.info(f'[SWITCH] s{dpid}: no output port for {src} -> {dst}')
            return

        if out_port in cfg['host_ports']:
            actions = [parser.OFPActionOutput(out_port)]
            self.add_flow(
                datapath, FORWARD_PRIORITY, parser.OFPMatch(eth_src=src, eth_dst=dst), actions)
        else:
            self.install_remote_rules(datapath, cfg, src, dst)
            actions = [parser.OFPActionOutput(out_port)]

        self.send_packet_out(datapath, msg, actions)

    def is_video_flow(self, ip_pkt, udp_pkt):
        return (
            ip_pkt is not None and ip_pkt.proto == in_proto.IPPROTO_UDP
            and udp_pkt is not None and udp_pkt.dst_port == VIDEO_UDP_PORT
        )

    def update_mac_to_port_table(self, dpid, src, in_port):
        table = self.mac_to_port.setdefault(dpid, {})
        if table.get(src) != in_port:
            table[src] = in_port
            self.logger.info(f'[SWITCH] s{dpid}: learned {src} on port {in_port}')

    def get_output_port(self, dpid, cfg, dst, in_port, is_video):
        local_port = self.mac_to_port.get(dpid, {}).get(dst)
        if local_port:
            return local_port
        
        if in_port not in cfg['host_ports']:
            return None
        
        return cfg['video'] if is_video else cfg['other']

    def install_remote_rules(self, datapath, cfg, src, dst):

        parser = datapath.ofproto_parser

        self.add_flow(
            datapath, FORWARD_PRIORITY,
            parser.OFPMatch(eth_src=src, eth_dst=dst),
            [parser.OFPActionOutput(cfg['other'])]
        )

        video_match = parser.OFPMatch(
            eth_src=src, eth_dst=dst,
            eth_type=ether_types.ETH_TYPE_IP,
            ip_proto=in_proto.IPPROTO_UDP,
            udp_dst=VIDEO_UDP_PORT
        )
        
        self.add_flow(
            datapath, VIDEO_PRIORITY, video_match, [parser.OFPActionOutput(cfg['video'])])

        self.logger.info(
            f'[SWITCH] s{datapath.id}: rules {src} -> {dst} (video -> {cfg["video"]}, other -> {cfg["other"]})')

    def handle_broadcast(self, datapath, msg, cfg, in_port):
        parser = datapath.ofproto_parser
        host_ports = cfg['host_ports']

        if in_port in host_ports:
            out_ports = [p for p in host_ports if p != in_port] + [cfg['other']]
        elif in_port == cfg['other']:
            out_ports = list(host_ports)
        else:
            return

        actions = [parser.OFPActionOutput(p) for p in out_ports]
        match = parser.OFPMatch(in_port=in_port, eth_dst=BROADCAST)
        self.add_flow(datapath, FORWARD_PRIORITY, match, actions)
        self.send_packet_out(datapath, msg, actions)

    def send_packet_out(self, datapath, msg, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id, in_port=msg.match['in_port'],
            actions=actions, data=data
        )
        datapath.send_msg(out)
