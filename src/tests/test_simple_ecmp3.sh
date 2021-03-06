#!/bin/bash
. env_utils.sh

env_init ${0##*/} # 0##*/ is the filename
sim_create hv1 || exit_test
sim_create hv2 || exit_test
sim_create hv3 || exit_test
sim_create hv4 || exit_test
sim_create ext1 || exit_test
net_create phy || exit_test
net_join phy hv1 || exit_test
net_join phy hv2 || exit_test
net_join phy hv3 || exit_test
net_join phy hv4 || exit_test
net_join phy ext1 || exit_test

# create logical switch and logical router first
etcd_ls_add LS-A
etcd_ls_add LS-B
etcd_lr_add LR-A

start_tuplenet_daemon hv1 192.168.100.2
GATEWAY=1 ONDEMAND=0 start_tuplenet_daemon hv2 192.168.100.3
GATEWAY=1 ONDEMAND=0 start_tuplenet_daemon hv3 192.168.100.4
GATEWAY=1 ONDEMAND=0 start_tuplenet_daemon hv4 192.168.100.5
start_tuplenet_daemon ext1 192.168.100.6
install_arp
wait_for_brint # waiting for building br-int bridge

sleep 5 # waiting for updating chassis to etcd by tuplenet

# only get a central_lr, test if script can add ecmp road
! add_ecmp_road hv2 192.168.100.51/24 || exit_test
# adding a new ecmp road
init_ecmp_road hv2 192.168.100.51/24 10.10.0.0/16 192.168.100.1 || exit_test
# test if failed to add ecmp road in same hv
! add_ecmp_road hv2 192.168.100.51/24 || exit_test
# link LS-A to LR-A
etcd_ls_link_lr LS-A LR-A 10.10.1.1 24 00:00:06:08:06:01
# link LS-B to LR-A
etcd_ls_link_lr LS-B LR-A 10.10.2.1 24 00:00:06:08:06:02
port_add hv1 lsp-portA || exit_test
etcd_lsp_add LS-A lsp-portA 10.10.1.2 00:00:06:08:07:01
port_add hv1 lsp-portB || exit_test
etcd_lsp_add LS-B lsp-portB 10.10.2.2 00:00:06:08:09:01
wait_for_flows_unchange # waiting for install flows

# send icmp to edge1(hv2) from hv1
ip_src=`ip_to_hex 10 10 1 2`
ip_dst=`ip_to_hex 192 168 100 51`
ttl=09
packet=`build_icmp_request 000006080701 000006080601 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portA "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt=`build_icmp_response 000006080601 000006080701 $ip_dst $ip_src $ttl bb75 8d10`
real_pkt=`get_tx_pkt hv1 lsp-portA`
verify_pkt $expect_pkt $real_pkt || exit_test

# send arp to edge1 from ext1
# send arp packet to request feedback
src_mac=`get_ovs_iface_mac ext1 br0`
src_mac=${src_mac//:} # convert xx:xx:xx:xx:xx:xx -> xxxxxxxxxxxx
sha=$src_mac
spa=`ip_to_hex 192 168 100 6`
tpa=`ip_to_hex 192 168 100 51`
# build arp request
packet=ffffffffffff${sha}08060001080006040001${sha}${spa}ffffffffffff${tpa}
inject_pkt ext1 br0 "$packet" || exit_test
wait_for_packet # wait for packet
reply_ha=f201c0a86433
expect_pkt=${sha}${reply_ha}08060001080006040002${reply_ha}${tpa}${sha}${spa}
real_pkt=`get_tx_last_pkt ext1 br0`
verify_pkt "$expect_pkt" "$real_pkt" || exit_test

# send icmp from ext1 to lsp-portA through edge1(hv2)
ip_src=`ip_to_hex 192 168 100 6`
ip_dst=`ip_to_hex 10 10 1 2`
ttl=09
packet=`build_icmp_request $src_mac $reply_ha $ip_src $ip_dst $ttl af85 8510`
inject_pkt ext1 br0 "$packet" || exit_test
wait_for_packet # wait for packet
ttl=07
expect_pkt=`build_icmp_request 000006080601 000006080701 $ip_src $ip_dst $ttl b185 8510`
real_pkt=`get_tx_last_pkt hv1 lsp-portA`
verify_pkt $expect_pkt $real_pkt || exit_test


add_ecmp_road hv3 192.168.100.53/24 || exit_test
wait_for_flows_unchange # waiting for install flows

# send icmp to edge2(hv3) from hv1
ip_src=`ip_to_hex 10 10 1 2`
ip_dst=`ip_to_hex 192 168 100 53`
ttl=09
packet=`build_icmp_request 000006080701 000006080601 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portA "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt=`build_icmp_response 000006080601 000006080701 $ip_dst $ip_src $ttl bb75 8d10`
real_pkt=`get_tx_last_pkt hv1 lsp-portA`
verify_pkt $expect_pkt $real_pkt || exit_test

# send icmp to edge1(hv2) from hv1 again
ip_src=`ip_to_hex 10 10 1 2`
ip_dst=`ip_to_hex 192 168 100 51`
ttl=09
packet=`build_icmp_request 000006080701 000006080601 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portA "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt=`build_icmp_response 000006080601 000006080701 $ip_dst $ip_src $ttl bb75 8d10`
real_pkt=`get_tx_last_pkt hv1 lsp-portA`
verify_pkt $expect_pkt $real_pkt || exit_test


add_ecmp_road hv4 192.168.100.57/24 || exit_test
wait_for_flows_unchange # waiting for install flows
# send icmp to edge2(hv4) from hv1
ip_src=`ip_to_hex 10 10 1 2`
ip_dst=`ip_to_hex 192 168 100 57`
ttl=09
packet=`build_icmp_request 000006080701 000006080601 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portA "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt=`build_icmp_response 000006080601 000006080701 $ip_dst $ip_src $ttl bb75 8d10`
real_pkt=`get_tx_last_pkt hv1 lsp-portA`
verify_pkt $expect_pkt $real_pkt || exit_test

# send icmp to edge1(hv2) from hv1 again
ip_src=`ip_to_hex 10 10 1 2`
ip_dst=`ip_to_hex 192 168 100 51`
ttl=09
packet=`build_icmp_request 000006080701 000006080601 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portA "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt=`build_icmp_response 000006080601 000006080701 $ip_dst $ip_src $ttl bb75 8d10`
real_pkt=`get_tx_last_pkt hv1 lsp-portA`
verify_pkt $expect_pkt $real_pkt || exit_test

# send arp to edge3 from ext1
# send arp packet to request feedback
src_mac=`get_ovs_iface_mac ext1 br0`
src_mac=${src_mac//:} # convert xx:xx:xx:xx:xx:xx -> xxxxxxxxxxxx
sha=$src_mac
spa=`ip_to_hex 192 168 100 6`
tpa=`ip_to_hex 192 168 100 57`
# build arp request
packet=ffffffffffff${sha}08060001080006040001${sha}${spa}ffffffffffff${tpa}
inject_pkt ext1 br0 "$packet" || exit_test
wait_for_packet # wait for packet
reply_ha=f201c0a86439
expect_pkt=${sha}${reply_ha}08060001080006040002${reply_ha}${tpa}${sha}${spa}
real_pkt=`get_tx_last_pkt ext1 br0`
verify_pkt "$expect_pkt" "$real_pkt" || exit_test

# send icmp from ext1 to lsp-portA through edge3(hv4)
ip_src=`ip_to_hex 192 168 100 6`
ip_dst=`ip_to_hex 10 10 1 2`
ttl=09
packet=`build_icmp_request $src_mac $reply_ha $ip_src $ip_dst $ttl af85 8510`
inject_pkt ext1 br0 "$packet" || exit_test
wait_for_packet # wait for packet
ttl=07
expect_pkt=`build_icmp_request 000006080601 000006080701 $ip_src $ip_dst $ttl b185 8510`
real_pkt=`get_tx_last_pkt hv1 lsp-portA`
verify_pkt $expect_pkt $real_pkt || exit_test

# test if we can delete edge node in other hv
remove_ecmp_road hv2 192.168.100.57/24 || exit_test
! remove_ecmp_road hv4 192.168.100.57/24 || exit_test
! remove_ecmp_road hv2 192.168.100.57/24 || exit_test
wait_for_flows_unchange # waiting for install flows
# send icmp to edge2(hv4) from hv2 by lsp-portB
ip_src=`ip_to_hex 10 10 2 2`
ip_dst=`ip_to_hex 192 168 100 57`
ttl=09
packet=`build_icmp_request 000006080901 000006080602 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portB "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt="" # should not get any packet
real_pkt=`get_tx_last_pkt hv1 lsp-portB`
verify_pkt $expect_pkt $real_pkt || exit_test

# send icmp to edge1(hv2) from hv1 by lsp-portB
ip_src=`ip_to_hex 10 10 2 2`
ip_dst=`ip_to_hex 192 168 100 51`
ttl=09
packet=`build_icmp_request 000006080901 000006080602 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portB "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt=`build_icmp_response 000006080602 000006080901 $ip_dst $ip_src $ttl bb75 8d10`
real_pkt=`get_tx_last_pkt hv1 lsp-portB`
verify_pkt $expect_pkt $real_pkt || exit_test

# remove the first road, now we only get the second edge(hv3) road
remove_ecmp_road hv2 192.168.100.51/24 || exit_test
wait_for_flows_unchange # waiting for install flows
# send icmp to edge1(hv2) from hv1 again by lsp-portB
ip_src=`ip_to_hex 10 10 2 2`
ip_dst=`ip_to_hex 192 168 100 53`
ttl=09
packet=`build_icmp_request 000006080901 000006080602 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portB "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt=`build_icmp_response 000006080602 000006080901 $ip_dst $ip_src $ttl bb75 8d10`
real_pkt=`get_tx_last_pkt hv1 lsp-portB`
verify_pkt $expect_pkt $real_pkt || exit_test


# remove the first road, now we only get the second edge(hv3) road
remove_ecmp_road hv3 192.168.100.53/24 || exit_test
wait_for_flows_unchange # waiting for install flows
# send icmp to edge1(hv2) from hv1 again by lsp-portB
ip_src=`ip_to_hex 10 10 2 2`
ip_dst=`ip_to_hex 192 168 100 53`
ttl=09
packet=`build_icmp_request 000006080901 000006080602 $ip_src $ip_dst $ttl af76 8510`
# we should not receive any feedback, expect_pkt = current packets
expect_pkt="`get_tx_pkt hv1 lsp-portB`"
inject_pkt hv1 lsp-portB "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
real_pkt="`get_tx_pkt hv1 lsp-portB`"
verify_pkt "$expect_pkt" "$real_pkt" || exit_test

# only get a central_lr, 2 LS, test if script can add ecmp road
! add_ecmp_road hv4 192.168.100.57/24 || exit_test
# adding a new ecmp road(on hv3)
init_ecmp_road hv4 192.168.100.57/24 10.10.0.0/16 192.168.100.1 || exit_test
wait_for_flows_unchange # waiting for install flows

# send icmp to edge3(hv4) from hv1 again by lsp-portB
ip_src=`ip_to_hex 10 10 2 2`
ip_dst=`ip_to_hex 192 168 100 57`
ttl=09
packet=`build_icmp_request 000006080901 000006080602 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portB "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt=`build_icmp_response 000006080602 000006080901 $ip_dst $ip_src $ttl bb75 8d10`
real_pkt=`get_tx_last_pkt hv1 lsp-portB`
verify_pkt $expect_pkt $real_pkt || exit_test

# send arp to edge2 from ext1
# send arp packet to request feedback
src_mac=`get_ovs_iface_mac ext1 br0`
src_mac=${src_mac//:} # convert xx:xx:xx:xx:xx:xx -> xxxxxxxxxxxx
sha=$src_mac
spa=`ip_to_hex 192 168 100 6`
tpa=`ip_to_hex 192 168 100 57`
# build arp request
packet=ffffffffffff${sha}08060001080006040001${sha}${spa}ffffffffffff${tpa}
inject_pkt ext1 br0 "$packet" || exit_test
wait_for_packet # wait for packet
reply_ha=f201c0a86439
expect_pkt=${sha}${reply_ha}08060001080006040002${reply_ha}${tpa}${sha}${spa}
real_pkt=`get_tx_last_pkt ext1 br0`
verify_pkt "$expect_pkt" "$real_pkt" || exit_test

# send icmp from ext1 to lsp-portA through edge3(hv4)
ip_src=`ip_to_hex 192 168 100 6`
ip_dst=`ip_to_hex 10 10 1 2`
ttl=09
packet=`build_icmp_request $src_mac $reply_ha $ip_src $ip_dst $ttl af85 8510`
inject_pkt ext1 br0 "$packet" || exit_test
wait_for_packet # wait for packet
ttl=07
expect_pkt=`build_icmp_request 000006080601 000006080701 $ip_src $ip_dst $ttl b185 8510`
real_pkt=`get_tx_last_pkt hv1 lsp-portA`
verify_pkt $expect_pkt $real_pkt || exit_test

# readd hv3 into edge, but using another ip
add_ecmp_road hv3 192.168.100.61/24 || exit_test
port_add hv1 lsp-portC || exit_test
etcd_lsp_add LS-B lsp-portC 10.10.2.3 00:00:06:08:09:05
wait_for_flows_unchange # waiting for install flows
# try to send icmp to edge1(hv2) from hv1, but edge2(hv3) cannot receive it due
# to incorrect hash, the packet has been forward to  edge3(hv4)
ip_src=`ip_to_hex 10 10 2 3`
ip_dst=`ip_to_hex 192 168 100 61`
ttl=09
packet=`build_icmp_request 000006080905 000006080602 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portC "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt="" # expect receive nothing
real_pkt=`get_tx_pkt hv1 lsp-portC`
verify_pkt $expect_pkt $real_pkt || exit_test


pmsg "terminate hv1"
kill_tuplenet_daemon hv1 -TERM
sleep 2
pmsg "restart hv1, consume symmetric_l3l4"
# consume symmetric_l3l4 in selecting dst edge(hv2, hv3)
HASH_FN=symmetric_l3l4 tuplenet_boot hv1 192.168.100.2
wait_for_flows_unchange # waiting for install flows
# send icmp to edge1(hv2) from hv1
ip_src=`ip_to_hex 10 10 2 3`
ip_dst=`ip_to_hex 192 168 100 61`
ttl=09
packet=`build_icmp_request 000006080905 000006080602 $ip_src $ip_dst $ttl af76 8510`
inject_pkt hv1 lsp-portC "$packet" || exit_test
wait_for_packet # wait for packet
ttl=fd
expect_pkt=`build_icmp_response 000006080602 000006080905 $ip_dst $ip_src $ttl bb75 8d10`
real_pkt=`get_tx_pkt hv1 lsp-portC`
verify_pkt $expect_pkt $real_pkt || exit_test

pass_test
