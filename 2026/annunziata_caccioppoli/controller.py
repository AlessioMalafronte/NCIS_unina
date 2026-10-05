from collections import deque
import json

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response

#definizione slice
PREMIUM = ['00:00:00:00:00:01', '00:00:00:00:00:02',
           '00:00:00:00:00:04', '00:00:00:00:00:05']
STANDARD = ['00:00:00:00:00:03', '00:00:00:00:00:06']

#mappa di forwarding statica
PORT_MAP = {
    1: {'00:00:00:00:00:01': 1, '00:00:00:00:00:02': 2, '00:00:00:00:00:03': 3,
        '00:00:00:00:00:04': 4, '00:00:00:00:00:05': 4, '00:00:00:00:00:06': 5},
    2: {'00:00:00:00:00:04': 1, '00:00:00:00:00:05': 2, '00:00:00:00:00:06': 3,
        '00:00:00:00:00:01': 4, '00:00:00:00:00:02': 4, '00:00:00:00:00:03': 5},
    3: {'00:00:00:00:00:01': 1, '00:00:00:00:00:02': 1,
        '00:00:00:00:00:04': 2, '00:00:00:00:00:05': 2},
    4: {'00:00:00:00:00:03': 1, '00:00:00:00:00:06': 2},
}

VIDEO_PORT = 9999   # UDP
VOICE_PORT = 5060   # UDP
PREMIUM_CAPACITY = 10000000  # bit/s

#porte di egress del percorso premium da monitorare: (dpid, port)
MONITORED = [(1, 4), (3, 2), (3, 1), (2, 4)]

MONITOR_INTERVAL = 5
WINDOW = 6              # campioni della media mobile
ALARM_CONSECUTIVE = 3   # intervalli fuori SLA prima dell'allarme


class SlicingController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(SlicingController, self).__init__(*args, **kwargs)
        self.datapaths = {}
        # stato monitor
        self.qhistory = {}
        self.qprev = {}       
        self.rates = {}     
        # SLA modificabili a runtime via REST
        self.sla = {'video_min': 6000000, 'voice_min': 2000000}
        self.alarm_count = {'video': 0, 'voice': 0}
        self.sla_status = {'video': 'OK', 'voice': 'OK'}
        kwargs['wsgi'].register(QoSRestApi, {'app': self})
        self.monitor_thread = hub.spawn(self._monitor)

    #Enforcer

    def add_flow(self, dp, priority, match, actions):
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority,
                                      match=match, instructions=inst))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        parser = dp.ofproto_parser
        dpid = dp.id
        self.datapaths[dpid] = dp

        #table-miss
        self.add_flow(dp, 0, parser.OFPMatch(), [])

        pmap = PORT_MAP.get(dpid, {})

        #topology slicing
        for tenant in (PREMIUM, STANDARD):
            for src in tenant:
                for dst in tenant:
                    if src == dst or dst not in pmap:
                        continue
                    match = parser.OFPMatch(eth_src=src, eth_dst=dst)
                    actions = [parser.OFPActionOutput(pmap[dst])]
                    self.add_flow(dp, 10, match, actions)

        # service slicing
        if dpid in (1, 2, 3):
            for dst in PREMIUM:
                if dst not in pmap:
                    continue
                out = pmap[dst]
                for udp_port, queue_id in ((VIDEO_PORT, 1), (VOICE_PORT, 2)):
                    match = parser.OFPMatch(eth_type=0x0800, ip_proto=17,
                                            udp_dst=udp_port, eth_dst=dst)
                    actions = [parser.OFPActionSetQueue(queue_id),
                               parser.OFPActionOutput(out)]
                    self.add_flow(dp, 20, match, actions)

        self.logger.info('slice rules installed on switch %d', dpid)

    #Monitor

    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                parser = dp.ofproto_parser
                ofp = dp.ofproto
                dp.send_msg(parser.OFPQueueStatsRequest(dp, 0,
                                                        ofp.OFPP_ANY,
                                                        ofp.OFPQ_ALL))
            hub.sleep(MONITOR_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPQueueStatsReply, MAIN_DISPATCHER)
    def queue_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        for stat in ev.msg.body:
            key = (dpid, stat.port_no, stat.queue_id)
            if (dpid, stat.port_no) not in MONITORED:
                continue
            prev = self.qprev.get(key)
            self.qprev[key] = stat.tx_bytes
            if prev is None:
                continue
            rate = (stat.tx_bytes - prev) * 8.0 / MONITOR_INTERVAL
            self.rates[str(key)] = int(rate)
            self.qhistory.setdefault(key, deque(maxlen=WINDOW)).append(rate)

        #verifica SLA sull'egress del core premium verso s2
        if dpid == 3:
            self._policy_check()

    #Policy engine

    def _avg(self, key):
        h = self.qhistory.get(key)
        return sum(h) / len(h) if h else 0.0

    def _policy_check(self):
        key_port = (3, 2)
        total = sum(self._avg((3, 2, q)) for q in (0, 1, 2))
        congested = total > 0.8 * PREMIUM_CAPACITY

        for name, queue_id, sla_key in (('video', 1, 'video_min'),
                                        ('voice', 2, 'voice_min')):
            avg = self._avg(key_port + (queue_id,))
            # violazione: rete congestionata ma la coda non riceve il minimo
            if congested and avg < 0.9 * self.sla[sla_key]:
                self.alarm_count[name] += 1
            else:
                self.alarm_count[name] = max(0, self.alarm_count[name] - 1)

            if self.alarm_count[name] >= ALARM_CONSECUTIVE:
                if self.sla_status[name] != 'VIOLATED':
                    self.logger.warning('SLA VIOLATION on %s slice '
                                        '(avg %.0f bit/s, min %d)',
                                        name, avg, self.sla[sla_key])
                self.sla_status[name] = 'VIOLATED'
            elif self.alarm_count[name] == 0:
                if self.sla_status[name] != 'OK':
                    self.logger.info('%s slice back to OK', name)
                self.sla_status[name] = 'OK'


#REST API

class QoSRestApi(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(QoSRestApi, self).__init__(req, link, data, **config)
        self.app = data['app']

    @route('qos', '/qos/stats', methods=['GET'])
    def get_stats(self, req, **kwargs):
        body = json.dumps({'rates_bps': self.app.rates,
                        'sla_status': self.app.sla_status,
                        'sla': self.app.sla})
        return Response(content_type='application/json', body=body.encode('utf-8'),
                        headerlist=[('Access-Control-Allow-Origin', '*')])

    @route('qos', '/qos/sla', methods=['POST'])
    def set_sla(self, req, **kwargs):
        try:
            new = json.loads(req.body)
            for k in ('video_min', 'voice_min'):
                if k in new:
                    self.app.sla[k] = int(new[k])
            return Response(status=200, body=json.dumps(self.app.sla))
        except Exception:
            return Response(status=400)