from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3

import json
from webob import Response
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.topology.api import get_all_link, get_all_switch

from l2_forwarding import L2Forwarding
from monitor import Monitor
from detection import Detection
from enforcement import Enforcement
from events import EventDatapathPortReport, EventMitigateFlow, EventTCP_SYN_FLOOD, EventApplyBlock, EventRemoveBlock


REST_API_NAME = "network_admin_api"


class AdminRestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(AdminRestController, self).__init__(req, link, data, **config)
        self.orchestrator = data[REST_API_NAME]


    @route("admin", "/api/v1/topology", methods=["GET"])
    def get_topology(self, req, **kwags):
        switches = get_all_switch(self.orchestrator)
        links = get_all_link(self.orchestrator)

        nodes = [{"dpid": f"{switch.dp.id:016x}"} for switch in switches]
        edges = [{
            "src": f"{link.src.dpid:016x}",
            "src_port": link.src.port_no,
            "dst": f"{link.dst.dpid:016x}",
            "dst_port": link.dst.port_no
        } for link in links]
        
        return Response(
            status = 200,
            content_type="application/json",
            json_body = json.dumps({
                "switches": nodes,
                "links": edges
            })
        )


    @route("admin", "/api/v1/policy/block", methods=["GET"])
    def get_block_policy(self, req, **kwags):
        dpid_param = req.GET.get("dpid", None)
        mac_param = req.GET.get("mac", None)

        try:
            dpid = int(dpid_param) if dpid_param else None
            mac = mac_param.lower() if mac_param else None
        except:
            return Response(
                status = 500,
                content_type="application/json",
                json_body = json.dumps(
                    {"error": "There was an error"}
                )
            )

        policies = self.orchestrator.get_policies(dpid = dpid, mac = mac)

        return Response(
            status = 200,
            content_type="application/json",
            json_body = json.dumps({
                "active_policies": policies
            })
        )
        

    @route("admin", "/api/v1/policy/block", methods=["POST"])
    def create_block_policy(self, req, **kwags):
        try:
            body = json.loads(req.body)
            mac_to_block = body.get("mac")
            duration = body.get("duration", 0)
            target_dpid = body.get("dpid", None)
            if not mac_to_block:
                return Response(
                    status = 400,
                    content_type="application/json",
                    json_body = json.dumps(
                        {"error": "Missing 'mac' field"}
                    )
                )

            self.orchestrator.add_block_policy(mac_to_block, duration, target_dpid)
        except Exception as e:
            return Response(
                status = 500,
                content_type="application/json",
                json_body = json.dumps(
                    {"error": "There was an error"}
                )
            )


    @route("admin", "/api/v1/policy/block/{mac}", methods=["DELETE"])
    def remove_block_policy_global(self, req, **kwargs):
        target_mac = kwargs.get("mac")
        self.orchestrator.remove_block_policy(target_mac)
        return Response(
            status = 200,
            content_type="application/json",
            json_body = json.dumps(
                {"status": "SUCCESS"}
            )
        )


    @route("admin", "/api/v1/policy/block/{mac}/switch/{dpid}", methods=["DELETE"])
    def remove_block_policy(self, req, **kwargs):
        target_mac = kwargs.get("mac")
        try:
            target_dpid = int(kwargs.get("dpid"))
        except ValueError:
            return Response(
                status = 400,
                content_type="application/json",
                json_body = json.dumps(
                    {"error": "INVALID DPID FORMAT"}
                )
            )

        try:
            self.orchestrator.remove_block_policy(target_mac, target_dpid)
        except ValueError:
            return Response(
                status = 400,
                content_type="application/json",
                json_body = json.dumps(
                    {"error": f"SWITCH WITH DPID {target_dpid} NOT FOUND"}
                )
            )

        return Response(
            status = 200,
            content_type="application/json",
            json_body = json.dumps(
                {"status": "SUCCESS"}
            )
        )



class ModularController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    _CONTEXTS = {
        "wsgi": WSGIApplication,
        "l2_forwarding": L2Forwarding,
        "monitor": Monitor,
        "detection": Detection,
        "enforcement": Enforcement
    }

    def __init__(self, *_args, **_kwargs):
        super(ModularController, self).__init__(*_args, **_kwargs)
        self.datapaths = {}

        self.wsgi = _kwargs["wsgi"]
        self.l2 = _kwargs["l2_forwarding"]
        self.monitor = _kwargs["monitor"]
        self.monitor.orchestrator = self
        self.detection = _kwargs["detection"]
        self.enforcement = _kwargs["enforcement"]

        self.monitor.register_observer(EventDatapathPortReport, self.detection.name)
        self.detection.register_observer(EventMitigateFlow, self.enforcement.name)
        self.detection.register_observer(EventTCP_SYN_FLOOD, self.enforcement.name)
        self.register_observer(EventApplyBlock, self.enforcement.name)
        self.register_observer(EventRemoveBlock, self.enforcement.name)
        self.wsgi.register(AdminRestController, {REST_API_NAME: self})

        self.logger.debug("[CONTROLLER] SETUP COMPLETED")


    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath

        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.info("[LOG] Switch registrato: %016x", datapath.id)
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.info("[LOG] Switch rimosso: %016x", datapath.id)
                self.datapaths.pop(datapath.id, None)


    def get_policies(self, dpid, mac):
        if dpid:
            datapaths = list(filter(lambda datapath: datapath.id == dpid, self.datapaths.values()))
        else:
            datapaths = list(self.datapaths.values())

        active_policies = self.enforcement.fetch_active_policies(datapaths = datapaths, mac = mac)
        return active_policies


    def add_block_policy(self, mac_to_block, duration = 0, target_dpid = None):
        if target_dpid is not None:
            if target_dpid in self.datapaths:
                target_datapaths = [self.datapaths[target_dpid]]
            else:
                raise ValueError
        else:
            target_datapaths = list(self.datapaths.values())

        self.send_event_to_observers(
            EventApplyBlock(
                mac_to_block = mac_to_block,
                duration = duration,
                target_datapaths = target_datapaths
            )
        )


    def remove_block_policy(self, target_mac, target_dpid = None):
        if target_dpid is not None:
            if target_dpid in self.datapaths:
                target_datapaths = [self.datapaths[target_dpid]]
            else:
                raise ValueError
        else:
            target_datapaths = list(self.datapaths.values())

        self.send_event_to_observers(
            EventRemoveBlock(
                target_mac = target_mac,
                target_datapaths = target_datapaths
            )
        )