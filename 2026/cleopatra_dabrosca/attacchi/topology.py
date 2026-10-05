from net_framework import NetworkBuilder


class BasicTopology(NetworkBuilder):
    def build_topology(self):
        self.add_host("h1", mac="00:00:00:00:00:01", ip="10.0.0.1")
        self.add_host("h2", mac="00:00:00:00:00:02", ip="10.0.0.2")
        self.add_host("h3", mac="00:00:00:00:00:03", ip="10.0.0.3")

        self.add_switch("s1")
        self.add_switch("s2")
        self.add_switch("s3")
        self.add_switch("s4")

        self.add_link("h1", "s1", bandwidth=10, delay=1)
        self.add_link("h2", "s2", bandwidth=10, delay=1)
        self.add_link("h3", "s4", bandwidth=10, delay=1)
        self.add_link("s1", "s3", bandwidth=10, delay=5)
        self.add_link("s2", "s3", bandwidth=10, delay=5)
        self.add_link("s3", "s4", bandwidth=10, delay=5)

        self.net.build()