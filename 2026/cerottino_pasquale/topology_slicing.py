from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import set_ev_cls, CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types


H1 = '00:00:00:00:00:01'
H2 = '00:00:00:00:00:02'
H3 = '00:00:00:00:00:03'
H4 = '00:00:00:00:00:04'

BROADCAST = 'ff:ff:ff:ff:ff:ff'

ALLOWED_PATH = {
    H1: [H3],
    H2: [H4],
    H3: [H1],
    H4: [H2],
}

EDGE_SWITCH = [1, 4]

CENTER_SWITCH = [2, 3]

EDGE_SWITCH_PORT_ASSOCIATIONS = {
    1: 3,
    2: 4,
    3: 1,
    4: 2,
}

CENTER_SWITCH_PORT_ASSOCIATIONS = {
    1: 2,
    2: 1
}

FORWARD_PRIORITY = 10
DROP_PRIORITY = 5


class TopologySlicing(app_manager.RyuApp):
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

    def add_drop_flow(self, datapath, src, dst):
        parser = datapath.ofproto_parser
        match = parser.OFPMatch(eth_src=src, eth_dst=dst)
        self.add_flow(datapath, DROP_PRIORITY, match, [])

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        in_port = msg.match['in_port']
        eth = packet.Packet(msg.data).get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src = eth.src
        dst = eth.dst          
        target = dst

        if dst == BROADCAST:
            partners = ALLOWED_PATH.get(src)
            if not partners:
                self.logger.info(f'[SWITCH] Path from {src} have no destinations')
                self.add_drop_flow(datapath, src, dst)
                return
            target = partners[0]

        if not self.is_allowed_path(src, target):
            self.logger.info(f'[SWITCH] Path from {src} to {target} not allowed')
            self.add_drop_flow(datapath, src, dst)
            return

        self.update_mac_to_port_table(dpid, src, in_port)

        out_port = self.get_output_port(dpid, target, in_port)
        if not out_port:
            self.logger.info(
                f'[SWITCH] Impossible assign output port for this input: src: {src}, port: {in_port}')
            return

        actions = [parser.OFPActionOutput(out_port)]
        match = parser.OFPMatch(in_port=in_port, eth_src=src, eth_dst=dst)

        if msg.buffer_id != ofproto.OFP_NO_BUFFER:
            self.add_flow(
                datapath, FORWARD_PRIORITY, match, actions, buffer_id=msg.buffer_id)
            return
        self.add_flow(datapath, FORWARD_PRIORITY, match, actions)

        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=data
        )
        datapath.send_msg(out)

    def get_association_table(self, dpid: int) -> dict:
        if dpid in EDGE_SWITCH:
            return EDGE_SWITCH_PORT_ASSOCIATIONS
        elif dpid in CENTER_SWITCH:
            return CENTER_SWITCH_PORT_ASSOCIATIONS
        return None

    def is_allowed_path(self, src, dst):
        allowed_destinations = ALLOWED_PATH.get(src)
        if not allowed_destinations or dst not in allowed_destinations:
            return False
        return True

    def update_mac_to_port_table(self, dpid, src, in_port):
        if not self.mac_to_port.get(dpid):
            self.mac_to_port.setdefault(dpid, {})
            self.logger.info(f'[SWITCH] Create record for switch {dpid}')

        if not self.mac_to_port[dpid].get(src):
            self.mac_to_port[dpid][src] = in_port
            self.logger.info(f'[SWITCH] Update switch {dpid}, set {src} input port to: {in_port}')

    def get_output_port(self, dpid, dst, in_port):
        out_port = self.mac_to_port[dpid].get(dst)
        if not out_port:
            association_table = self.get_association_table(dpid)
            if not association_table:
                return None

            out_port = association_table.get(in_port)
            if not out_port:
                return None

            self.update_mac_to_port_table(dpid, dst, out_port)

        return out_port
