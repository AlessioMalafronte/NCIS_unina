from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet


class BaseController(app_manager.RyuApp):

    # utilizza OpenFlow 1.3
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(BaseController, self).__init__(*args, **kwargs)

        # memorizza le porte associate ai mac
        self.mac_to_port = {}

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

        # recupera il messaggio PacketIn
        msg = ev.msg
        datapath = msg.datapath

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # recupera l identificativo dello switch
        dpid = datapath.id

        # recupera la porta di ingresso
        in_port = msg.match["in_port"]

        # analizza il pacchetto ricevuto
        pkt = packet.Packet(msg.data)

        # estrae l header ethernet
        eth = pkt.get_protocol(ethernet.ethernet)

        src = eth.src
        dst = eth.dst

        # crea la tabella associata allo switch se non esiste
        self.mac_to_port.setdefault(dpid, {})

        # associa il mac sorgente alla porta di ingresso
        self.mac_to_port[dpid][src] = in_port

        # utilizza la porta nota se il mac destinazione è già stato appreso
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

        # inoltra il pacchetto che ha generato il PacketIn
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data
        )

        # invia il PacketOut allo switch
        datapath.send_msg(out)
