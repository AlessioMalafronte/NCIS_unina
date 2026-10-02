from ryu.controller import event


class EventDatapathPortReport(event.EventBase):
    def __init__(self, datapath, flows):
        super(EventDatapathPortReport, self).__init__()
        self.datapath = datapath
        self.flows = flows


class EventMitigateFlow(event.EventBase):
    def __init__(self, datapath, port_no, target_mac, duration):
        super(EventMitigateFlow, self).__init__()
        self.datapath = datapath
        self.port_no = port_no
        self.target_mac = target_mac
        self.duration = duration


class EventTCP_SYN_FLOOD(event.EventBase):
    def __init__(self, datapath, port_no, target_mac):
        super(EventTCP_SYN_FLOOD, self).__init__()
        self.datapath = datapath
        self.port_no = port_no
        self.target_mac = target_mac


class EventApplyBlock(event.EventBase):
    def __init__(self, mac_to_block, duration, target_datapaths):
        super(EventApplyBlock, self).__init__()
        self.mac_to_block = mac_to_block
        self.duration = duration
        self.target_datapaths = target_datapaths


class EventRemoveBlock(event.EventBase):
    def __init__(self, target_mac, target_datapaths):
        super(EventRemoveBlock, self).__init__()
        self.target_mac = target_mac
        self.target_datapaths = target_datapaths