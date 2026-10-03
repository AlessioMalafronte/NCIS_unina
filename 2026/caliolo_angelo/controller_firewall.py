# Copyright (C) 2016 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#_____________________________________
# Versione 2.0                        |
# Specifiche di base soddisfatte      |
# Riattiva il traffico dopo tot tempo |
# Nessun effetto Piracy Shield        |
#_____________________________________|

from operator import attrgetter

from ryu import cfg
from ryu.app import simple_switch_13
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub

class SimpleMonitor13(simple_switch_13.SimpleSwitch13):

    MAX_NODES = 100
    THRESHOLD = 50*1000*1000 #50 MBits/s
    SLEEP_TIME = 10 # Periodo di attesa tra i monitoraggi
    WAIT_TIME = 5 # Numero di cicli (SLEEP_TIME) di attesa prima che si ripristini il traffico su una porta

    CONF = cfg.CONF
    CONF.ofp_tcp_listen_port = 6633
    
    def __init__(self, *args, **kwargs):
        super(SimpleMonitor13, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.database = [None] * self.MAX_NODES
        self.sleep_counter = [[-1 for _ in range(self.MAX_NODES)] for _ in range(self.MAX_NODES)]
        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.debug('register datapath: %016x', datapath.id)
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.debug('unregister datapath: %016x', datapath.id)
                del self.datapaths[datapath.id]

    def _monitor(self):
        while True:
            for dp in sorted (self.datapaths.values(), key=attrgetter('id')):
                self._request_stats(dp)
            hub.sleep(self.SLEEP_TIME)

    def _request_stats(self, datapath):
        self.logger.debug('send stats request: %016x', datapath.id)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        if self.database[datapath.id] == None:
            self.database[datapath.id] = [None]*(len(body)+1)
        measures = self.database[datapath.id]

        self.logger.info('||-----------------------------------------------------------------------------------------------------------------------------||')
        self.logger.info('||datapath             port '
                         '  rx-pkts      rx-bytes  rx-error   rx-throughput '
                         '  tx-pkts      tx-bytes  tx-error   tx-throughput||')
        self.logger.info('||---------------- -------- '
                         '--------- ------------- --------- --------------- '
                         '--------- ------------- --------- ---------------||')


        for stat in sorted(body, key=attrgetter('port_no')):

            if stat.port_no == int('FFFFFFFE', 16):
                port = 0
            else:
                port = stat.port_no

            if measures[port] == None or measures[port].rx_packets > stat.rx_packets:
                measures[port] = stat

            # Calcolo throughput
            tx_throughput = (stat.tx_bytes-measures[port].tx_bytes)/self.SLEEP_TIME*8
            rx_throughput = (stat.rx_bytes-measures[port].rx_bytes)/self.SLEEP_TIME*8

            if tx_throughput < 1000:
                tx_throughput_str = str(round(tx_throughput,2)) + " Bits/s"
            elif tx_throughput < 1000000:
                tx_throughput_str = str(round(tx_throughput/1000, 2)) + " KBits/s"
            elif tx_throughput < 1000000000:
                tx_throughput_str = str(round(tx_throughput/1000000, 2)) + " MBits/s"
            else:
                tx_throughput_str = str(round(tx_throughput/1000000000, 2)) + " GBits/s"

            if rx_throughput < 1000:
                rx_throughput_str = str(round(rx_throughput,2)) + " Bits/s"
            elif rx_throughput < 1000000:
                rx_throughput_str = str(round(rx_throughput/1000, 2)) + " KBits/s"
            elif rx_throughput < 1000000000:
                rx_throughput_str = str(round(rx_throughput/1000000, 2)) + " MBits/s"
            else:
                rx_throughput_str = str(round(rx_throughput/1000000000, 2)) + " GBits/s"
            
            self.logger.info('||' + '{:016d}{:> 9X}{:> 10n}{:> 14n}{:> 10n}{:>16s}{:> 10n}{:> 14n}{:> 10n}{:>16s}'.format(
                             ev.msg.datapath.id, stat.port_no,
                             stat.rx_packets, stat.rx_bytes, stat.rx_errors, rx_throughput_str,
                             stat.tx_packets, stat.tx_bytes, stat.tx_errors, tx_throughput_str) + '||')
            
            # Notifica su S3 in caso di attacco
            if rx_throughput > self.THRESHOLD:
                self.logger.warning('Anomaly detected! High traffic on port %d', stat.port_no)
                # Crea una regola di match per tutti i pacchetti provenienti dalla porta `port_no`
                match = parser.OFPMatch(in_port=stat.port_no)
                # Nessuna azione, drop dei pacchetti
                actions = []
                # Istruzione per non eseguire nessuna azione (drop)
                inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
                # Crea un messaggio OFPFlowMod per aggiungere la regola
                mod = parser.OFPFlowMod(datapath=datapath, priority=2, match=match, instructions=inst)
                # Invia il messaggio allo switch
                datapath.send_msg(mod)
                self.sleep_counter[datapath.id][port] = self.WAIT_TIME
            elif self.sleep_counter[datapath.id][port] == 0:
                self.logger.warning('Resumed traffic on port %d', stat.port_no)
                match = parser.OFPMatch(in_port=stat.port_no)
                # Ripristina il flusso predefinito con azione forward (invio normale)
                actions = [parser.OFPActionOutput(ofproto.OFPP_NORMAL)]  # Azione per inviare normalmente i pacchetti
                # Istruzione per non eseguire nessuna azione (drop)
                inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
                # Crea un messaggio OFPFlowMod per ripristinare il flusso
                mod = parser.OFPFlowMod(datapath=datapath, priority=1, match=match, instructions=inst, command=ofproto.OFPFC_MODIFY)
                # Invia il messaggio allo switch per ripristinare il traffico
                datapath.send_msg(mod)
            else:
                self.sleep_counter[datapath.id][port] -= 1
            
            # Salviamo e aggiorniamo le misurazioni
            measures[port] = stat
            self.database[datapath.id] = measures