from ryu.base import app_manager
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from events import EventDatapathPortReport, EventMitigateFlow, EventTCP_SYN_FLOOD


class Detection(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    ALPHA = 0.5
    K_FACTOR = 3.0
    MIN_THRESHOLD_MBIT_SECOND = 5.0
    MIN_THRESHOLD_PACKETS_COUNT = 300
    FLOOD_SOURCE_THRESHOLD = 0.6
    SPOOFED_MAC_THRESHOLD = 100

    PROTECTED_MACS = ["00:00:00:00:00:03"]

    
    def __init__(self, *_args, **_kwargs):
        super(Detection, self).__init__(*_args, **_kwargs)
        self.ports_ema = {}
        self.attack_history = {}
        self.anomalies_history = {}

        self.hard_threshold = 8.0  # 80% della capacità di link

        self.pending_flow_xids = {}

        self.logger.debug("[DETECTION] SETUP COMPLETED")


    @set_ev_cls(EventDatapathPortReport)
    def _detect_anomalies(self, ev):
        datapath = ev.datapath
        flows = ev.flows

        for in_port, port_flows in flows.items():
            port_ema = self.ports_ema.setdefault(in_port, {
                "bytes_ema": {},
                "packets_count_ema": None
            })

            suspected_flood_sources = {}
            total_packets_count = 0

            for flow in port_flows:
                eth_src = flow["eth_src"]
                eth_dst = flow["eth_dst"]
                delta_time = flow["delta_time"]
                delta_bytes = flow["delta_bytes"]
                delta_packets_count = flow["delta_packets_count"]

                if eth_src in self.PROTECTED_MACS:
                    continue

                flow_key = (eth_src, eth_dst)

                if delta_time:
                    throughput_bps = (delta_bytes*8) / delta_time
                else:
                    throughput_bps = 0
                
                throughput_mbps = throughput_bps / (1024 * 1024)

                bytes_ema = port_ema["bytes_ema"].setdefault(flow_key, throughput_mbps)
                bytes_dynamic_threshold = max(bytes_ema * self.K_FACTOR, self.MIN_THRESHOLD_MBIT_SECOND)

                if throughput_mbps > bytes_dynamic_threshold:
                    self.logger.warning("[DETECTION] FOUND THROUGHPUT DYNAMIC ANOMALY (SWITCH %016x - PORT %d) [%.2f Mbps > %.2f Mbps]",
                        datapath.id, in_port, throughput_mbps, bytes_dynamic_threshold
                    )

                    self.single_source_attack_detected(datapath, in_port, eth_src)
                elif throughput_mbps > self.hard_threshold:
                    anomalies_count = self.anomalies_history[flow_key] = self.anomalies_history.get(flow_key, 0) + 1

                    self.logger.warning("[DETECTION] FOUND THROUGHPUT STATIC ANOMALY (SWITCH %016x - PORT %d) [%.2f Mbps > %.2f Mbps] | COUNT: %d",
                        datapath.id, in_port, throughput_mbps, self.hard_threshold, anomalies_count
                    )

                    if anomalies_count >= 3:
                        self.single_source_attack_detected(datapath, in_port, eth_src)
                else:
                    self.anomalies_history.pop(flow_key, None)

                    new_bytes_ema = self.ALPHA * throughput_mbps + (1 - self.ALPHA) * bytes_ema
                    self.ports_ema[in_port]["bytes_ema"][flow_key] = new_bytes_ema

                if delta_packets_count > 0:
                    avg_packet_size = delta_bytes / delta_packets_count
                else:
                    avg_packet_size = 0
                
                if avg_packet_size < 90:
                    suspected_flood_sources[flow_key] = delta_packets_count

                total_packets_count += delta_packets_count

            packets_count_ema = port_ema["packets_count_ema"] if port_ema["packets_count_ema"] is not None else total_packets_count
            packets_count_dynamic_threshold = max(packets_count_ema * self.K_FACTOR, self.MIN_THRESHOLD_PACKETS_COUNT)

            packets_count_anomaly = total_packets_count > packets_count_dynamic_threshold
            if packets_count_anomaly:
                self.logger.warning("[DETECTION] FOUND PACKETS COUNT ANOMALY (SWITCH %016x - PORT %d) [%d > %d]",
                    datapath.id, in_port, total_packets_count, packets_count_dynamic_threshold
                )

                for flow_key, packets_count in suspected_flood_sources.items():
                    packets_count_percentage = packets_count / total_packets_count

                    if packets_count_percentage > self.FLOOD_SOURCE_THRESHOLD:
                        eth_src, eth_dst = flow_key

                        self.logger.warning("[DETECTION] FOUND SINGLE FLOOD SOURCE %s FOR SWITCH %016x (PORT %d)",
                            eth_src, datapath.id, in_port
                        )

                        self.single_source_attack_detected(datapath, in_port, eth_src)
                        return

            is_spoofed = len(suspected_flood_sources.keys()) > self.SPOOFED_MAC_THRESHOLD

            if packets_count_anomaly or is_spoofed:
                self.logger.warning("[DETECTION] FOUND SPOOFED FLOOD ATTACK FOR SWITCH %016x (PORT %d)",
                    datapath.id, in_port
                )

                self.spoofed_attack_detected(datapath, in_port)
            else:
                new_packets_count_ema = self.ALPHA * total_packets_count + (1 - self.ALPHA) * packets_count_ema
                self.ports_ema[in_port]["packets_count_ema"] = new_packets_count_ema


    def single_source_attack_detected(self, datapath, in_port, eth_src):
        key = (datapath.id, eth_src)
        count = self.attack_history[key] = self.attack_history.get(key, 0) + 1

        if count == 1:
            duration = 30
        elif count == 2:
            duration = 300
        else:
            duration = 3600

        self.send_event_to_observers(
            EventMitigateFlow(datapath, in_port, eth_src, duration)
        )

    def spoofed_attack_detected(self, datapath, in_port):
        self.send_event_to_observers(
            EventTCP_SYN_FLOOD(datapath, in_port, None)
        )