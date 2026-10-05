from ryu.base import app_manager
from ryu.lib import hub
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from events import EventMitigateFlow, EventTCP_SYN_FLOOD, EventApplyBlock, EventRemoveBlock


class Enforcement(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]


    def __init__(self, *_args, **_kwargs):
        super(Enforcement, self).__init__(*_args, **_kwargs)

        self.stats_waiters = dict()
        self.stats_cache = dict()

        self.logger.debug("[ENFORCEMENT] SETUP COMPLETED")


    def fetch_active_policies(self, datapaths, mac):
        results = list()

        for datapath in datapaths:
            parser = datapath.ofproto_parser

            if mac:
                match = parser.OFPMatch(eth_src = mac)
            else:
                match = parser.OFPMatch()
            request = parser.OFPFlowStatsRequest(
                datapath = datapath,
                match = match
            )
            
            datapath.send_msg(request)
            xid = request.xid
            
            event = hub.Event()
            self.stats_waiters[xid] = event
            event.wait(timeout = 2.0)

            raw_flows = self.stats_cache.pop(xid, [])
            self.stats_waiters.pop(xid, None)

            for stat in raw_flows:
                if stat.priority == 200:
                    results.append({
                        "dpid": datapath.id,
                        "port_no": stat.match.get("in_port", None),
                        "target_mac": stat.match.get("eth_src"),
                        "duration": stat.hard_timeout
                    })

        return results


    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        xid = ev.msg.xid
        if xid in self.stats_waiters:
            self.stats_cache[xid] = ev.msg.body
            self.stats_waiters[xid].set()


    @set_ev_cls(EventMitigateFlow)
    def _mitigation_handler(self, ev):
        datapath = ev.datapath
        port_no = ev.port_no
        target_mac = ev.target_mac
        duration = ev.duration

        if port_no:
            self.logger.info(
                "[ENFORCEMENT] MITIGATION ON SWITCH %016x (PORT %d): DROP on MAC %s (%ds Timeout)",
                datapath.id, port_no, target_mac, duration
            )
        else:
            self.logger.info(
                "[ENFORCEMENT] MITIGATION ON SWITCH %016x: DROP on MAC %s (%ds Timeout)",
                datapath.id, target_mac, duration
            )

        self._apply_drop(datapath, port_no, target_mac, duration)



    @set_ev_cls(EventTCP_SYN_FLOOD)
    def _tcp_syn_flood_handler(self, ev):
        datapath = ev.datapath
        port_no = ev.port_no
        target_mac = ev.target_mac

        if target_mac:
            self.logger.info(
                "[ENFORCEMENT] MITIGATION ON SWITCH %016x (PORT %d): DROP on MAC %s",
                datapath.id, port_no, target_mac
            )

            self._apply_drop(datapath, port_no, target_mac, 0)
        else:
            self.logger.info(
                "[ENFORCEMENT] MITIGATION ON SWITCH %016x (PORT %d): DROP on SYN",
                datapath.id, port_no
            )
            
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser

            match = parser.OFPMatch(
                in_port = port_no,
                eth_type = 0x0800,
                ip_proto = 6,
                tcp_flags = (0x002, 0x012)
            )

            instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, [])]

            mod = parser.OFPFlowMod(
                datapath = datapath,
                priority = 201,
                match = match,
                instructions = instructions,
                command = ofproto.OFPFC_ADD,
                idle_timeout = 30,
                hard_timeout = 30,
                flags = ofproto.OFPFF_SEND_FLOW_REM
            )
            datapath.send_msg(mod)


    @set_ev_cls(EventApplyBlock)
    def _block_policy_handler(self, ev):
        mac_to_block = ev.mac_to_block
        duration = ev.duration
        target_datapaths = ev.target_datapaths

        for datapath in target_datapaths:
            self.logger.info(
                "[ENFORCEMENT] DROP POLICY FOR SWITCH %016x FOR MAC %s (%d TIMEOUT)",
                datapath.id, mac_to_block, duration
            )

            self._apply_drop(datapath, None, mac_to_block, duration)


    def _apply_drop(self, datapath, in_port, eth_src, duration):
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser

            if in_port is not None:
                match = parser.OFPMatch(in_port = in_port, eth_src = eth_src)
            else:
                match = parser.OFPMatch(eth_src = eth_src)

            instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, [])]

            mod = parser.OFPFlowMod(
                datapath = datapath,
                priority = 200,
                match = match,
                instructions = instructions,
                command = ofproto.OFPFC_ADD,
                hard_timeout = duration,
                flags = ofproto.OFPFF_SEND_FLOW_REM
            )
            datapath.send_msg(mod)


    @set_ev_cls(EventRemoveBlock)
    def _remove_policy_handler(self, ev):
        target_mac = ev.target_mac
        target_datapaths = ev.target_datapaths

        for datapath in target_datapaths:
            self.logger.info(
                "[ENFORCEMENT] DROP POLICY REMOVED FOR SWITCH %016x FOR MAC %s",
                datapath.id, target_mac
            )

            self._remove_block_policy(datapath, target_mac)


    def _remove_block_policy(self, datapath, eth_src):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch(eth_src = eth_src)

        mod = parser.OFPFlowMod(
            datapath = datapath,
            priority = 200,
            match = match,
            command = ofproto.OFPFC_DELETE,
            out_port = ofproto.OFPP_ANY,
            out_group = ofproto.OFPG_ANY
        )
        datapath.send_msg(mod)