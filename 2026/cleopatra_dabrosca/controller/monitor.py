from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from events import EventDatapathPortReport


MONITOR_INTERVAL = 1


class Monitor(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    INTERVAL = MONITOR_INTERVAL

    def __init__(self, *_args, **_kwargs):
        super(Monitor, self).__init__(*_args, **_kwargs)
        self.flow_history = {}
        self.pending_flow_xids = set()

        self.orchestrator = _kwargs.get("orchestrator", None)

        hub.spawn(self._monitor_loop)

        self.logger.debug("[MONITOR] SETUP COMPLETED")


    def _monitor_loop(self):
        self.logger.debug("[MONITOR] LOOP START")

        assert self.orchestrator and hasattr(self.orchestrator, "datapaths"), "[MONITOR] ORCHESTRATOR ERROR"

        while True:
            for datapath in self.orchestrator.datapaths.values():
                parser = datapath.ofproto_parser
                request = parser.OFPFlowStatsRequest(datapath)
                datapath.send_msg(request)
                self.pending_flow_xids.add(request.xid)
            hub.sleep(self.INTERVAL)


    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _port_stats_handler(self, ev):
        xid = ev.msg.xid
        
        if xid not in self.pending_flow_xids:
            return

        self.pending_flow_xids.remove(xid)
        
        body = ev.msg.body
        datapath = ev.msg.datapath
        dpid = datapath.id

        flows = dict()

        for stat in body:
            if stat.priority <= 100:
                eth_src = stat.match.get("eth_src")
                eth_dst = stat.match.get("eth_dst")

                if not eth_src or not eth_dst:
                    continue

                in_port = stat.match.get("in_port")
                flow_key = (dpid, in_port, eth_src, eth_dst)

                if in_port not in flows.keys():
                    flows[in_port] = list()

                current_bytes = stat.byte_count
                current_packets_count = stat.packet_count
                current_time = stat.duration_sec + stat.duration_nsec / 10**9

                if flow_key in self.flow_history:
                    history = self.flow_history[flow_key]

                    delta_time = current_time - history["time"]
                    delta_bytes = current_bytes - history["bytes"]
                    delta_packets_count = current_packets_count - history["packets_count"]
                else:
                    delta_time = current_time
                    delta_bytes = current_bytes
                    delta_packets_count = current_packets_count

                self.logger.info("[MONITOR] Switch DPID: %016x (Port: %d) | %s -> %s | Duration: %.2f s | Bytes: %d | Packets Count: %d",
                    dpid, in_port, eth_src, eth_dst, delta_time, delta_bytes, delta_packets_count
                )

                flows[in_port].append({
                    "eth_src": eth_src,
                    "eth_dst": eth_dst,
                    "delta_time": delta_time,
                    "delta_bytes": delta_bytes,
                    "delta_packets_count": delta_packets_count
                })

                self.flow_history[flow_key] = {
                    "time": current_time,
                    "bytes": current_bytes,
                    "packets_count": current_packets_count
                }

        self.send_event_to_observers(
            EventDatapathPortReport(
                datapath = datapath,
                flows = flows
            )
        )


    @set_ev_cls(ofp_event.EventOFPFlowRemoved, MAIN_DISPATCHER)
    def _flow_removed_handler(self, ev):
        datapath = ev.msg.datapath
        in_port = ev.msg.match.get("in_port")
        eth_src = ev.msg.match.get("eth_src")
        eth_dst = ev.msg.match.get("eth_dst")

        flow_key = (datapath.id, in_port, eth_src, eth_dst)
        self.flow_history.pop(flow_key, None)
