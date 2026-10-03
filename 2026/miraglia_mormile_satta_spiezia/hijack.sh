#!/bin/bash

if [ "$1" = "JD" ]; then
    ip route add local 172.16.0.0/24 dev lo

elif [ "$1" = "rf3" ]; then
    ip route add 172.16.0.0/25 via 192.168.0.251 dev rf3-eth1
    ip route add 172.16.0.128/25 via 192.168.0.251 dev rf3-eth1
    ip route get 172.16.0.251
    vtysh -c "configure terminal" -c "router bgp 300" -c "address-family ipv4 unicast" -c "network 172.16.0.0/25" -c "network 172.16.0.128/25" -c "exit-address-family" -c "end" 
elif [ "$1" = "rf1" ]; then
    vtysh -c "show bgp ipv4 unicast"
else echo "Nodo di rete utilizzato non corretto. (Nodo usato: $1 )"
fi