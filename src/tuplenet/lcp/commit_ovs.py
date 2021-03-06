import os
import json
import subprocess
import logging
import threading
import struct, socket
import time, random, string
import logicalview as lgview
import ovsdbman as dbm
from pyDatalog import pyDatalog
from onexit import on_parent_exit
from tp_utils.run_env import get_extra

logger = logging.getLogger(__name__)
flow_lock = threading.Lock()
TP_TUNNEL_PORT_NAME_PREFIX = "tupleNet-"
# In  OpenFlow  1.0  and  1.1,  re-adding a flow always resets the
# flow's packet and byte counters to 0.
OPENFLOW_VER = "OpenFlow12"

class OVSToolErr(Exception):
    pass

def call_popen(cmd, commu=None, shell=False):
    child = subprocess.Popen(cmd, shell=shell, stdout=subprocess.PIPE,
                             stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    if commu is None:
        output = child.communicate()
    else:
        output = child.communicate(commu)
    if child.returncode:
        raise RuntimeError("error executing %s" % (' '.join(cmd)))
    if len(output) == 0 or output[0] is None:
        output = ""
    else:
        output = output[0].decode("utf8").strip()
    return output

def call_ovsprog(prog, args_list, commu=None):
    cmd = [prog, "--timeout=5"] + args_list
    retry_n = 0
    while True:
        try:
            return call_popen(cmd, commu)
        except Exception as err:
            retry_n += 1
            if retry_n == 3:
                raise err
            continue

def ovs_ofctl(*args):
    return call_ovsprog("ovs-ofctl", list(args))

def aggregate_flows(flows):
    length = 500
    flow_seg = [flows[x : x + length] for x in range(0, len(flows), length)]
    for seg in flow_seg:
        yield '\n'.join(seg)

def ovs_ofctl_delflows_batch(br, flows):
    for flow_combind in aggregate_flows(flows):
        call_ovsprog("ovs-ofctl", ['del-flows', '-O', OPENFLOW_VER, br,
                                   '--strict', '-'],
                     commu=flow_combind)


def ovs_ofctl_addflows_batch(br, flows):
    for flow_combind in aggregate_flows(flows):
        call_ovsprog("ovs-ofctl", ['add-flow', '-O', OPENFLOW_VER, br, '-'],
                     commu=flow_combind)

def ovs_vsctl(*args):
    return call_ovsprog("ovs-vsctl", list(args))

def ovs_get_iface_property(iface, col_name):
    try:
        p = ovs_vsctl('get', 'interface', iface, col_name)
        p = p.strip("\"")
    except:
        return
    return p

def create_ovsport(br, portname, port_type, properties = {}):
    plist = ['{}={}'.format(k,v) for k,v in properties.items()]
    cmd = ['add-port', br, portname, '--', 'set', 'interface',
           portname, 'type={}'.format(port_type)] + plist
    try:
        ovs_vsctl(*cmd)
    except Exception as err:
        logger.error('cannot create ovsport %s, err:%s', portname, err)
        return False
    return True

def delete_ovsport(portname):
    try:
        ovs_vsctl('del-port', portname)
    except Exception as err:
        logger.warning("failed to delete ovsport %s, err:%s", portname, err)
        return False
    return True

def is_br_exist(br):
    try:
        ovs_vsctl('br-exists', br)
    except:
        return False
    return True

def parse_map(map_list):
    ret_map = {}
    if map_list[0] != 'map':
        return None
    for entry in map_list[1]:
        ret_map[entry[0]] = entry[1]
    return ret_map

def update_ovsport(record, entity_zoo):
    action_type = record[0]
    if action_type in ['new', 'initial', 'delete', 'old', 'insert']:
        # some operations may not contain some essential fields
        if not isinstance(record[1], int) or \
           record[2] == None or record[3] == None:
            logger.debug('action %s does not container enough info, msg:%s',
                         action_type, record)
            return
    else:
        logger.warning("unknow action_type:%s", action_type)
        return

    logger.info("ovsport action type:%s", action_type)

    ofport = record[1]
    name = record[2]
    external_ids = record[3]
    port_type = record[4]

    # adding an interal port may imply that an new bridge was created,
    # we should update zoo's version to test if tuplenet should rebuild
    # patchports.
    if port_type == 'internal':
        entity_zoo.update_version_force()

    if external_ids.has_key('iface-id'):
        uuid = external_ids['iface-id']
        is_remote = False
        entity_type = lgview.LOGICAL_ENTITY_TYPE_OVSPORT
    elif external_ids.has_key('chassis-id'):
        uuid = external_ids['chassis-id']
        is_remote = True
        entity_type = lgview.LOGICAL_ENTITY_TYPE_OVSPORT_CHASSIS
    else:
        logger.info('external_ids has no chassis-id or iface-id, record:%s',
                    record)
        return

    if action_type in ['old', 'delete']:
        logger.info("try to move port %s to sink uuid:%s", name, uuid)
        entity_zoo.move_entity2sink(entity_type, name)
    else:
        if ofport < 0:
            logger.info("do not accept ovsport %s which has negative ofport %d",
                        name, ofport)
            return
        logger.info("try to add ovsport entity %s ofport:%d, uuid:%s in zoo",
                    name, ofport, uuid)
        entity_zoo.add_entity(entity_type, name, uuid, ofport, is_remote)
    return

# NOTE: this function would be consume by ovsdbman
@dbm.ovsdb_resp_handle
def handle_interface_update(data, entity_zoo):
    iface_records = dbm.iface_monitor_parse(data)
    for record in iface_records:
        update_ovsport(record, entity_zoo)


def monitor_ovsdb(entity_zoo, extra):
    logger.info("start monitoring ovsdb change")
    ovsdb = dbm.OvsdbMan(extra['options']['TUPLENET_OVSDB_PATH'])
    ovsdb.run(pyDatalog.Logic, extra['logic'])
    ret = ovsdb.monitor_interfaces(handle_interface_update, entity_zoo)
    if ret is False:
        raise dbm.OVSDBErr("failed to monitor interfaces update")
    return ovsdb

def clean_ovs_flows(br = 'br-int'):
    try:
        ovs_ofctl("del-flows", br)
    except Exception as err:
        logger.error("failed to clean bridge %s flows", br)
        raise OVSToolErr("failed to clean ovs flows")
    else:
        logger.info("clean all flows in %s", br)

def insert_ovs_ipfix(br = 'br-int'):
    try:
        ovs_vsctl('clear', 'bridge', br, 'ipfix')

        options = get_extra()['options']
        if 'IPFIX_CFG' in options:
            cfg = options['IPFIX_CFG']
            logger.debug('add ipfix record {}'.format(cfg))

            ovs_vsctl('--', 'set', 'Bridge', br, 'ipfix=@i', '--', '--id=@i', \
                      'create', 'IPFIX', 'targets=\"{}\"'.format(cfg['collector']), \
                      'obs_domain_id={}'.format(cfg['domain_id']), \
                      'obs_point_id={}'.format(cfg['point_id']), \
                      'sampling={}'.format(cfg['sampling_rate']), \
                      'other_config:enable-tunnel-sampling=flase')
    except Exception as e:
        logger.error('failed to alter ipfix record for %s: %s', br, e)
        raise OVSToolErr('failed to alter ipfix record: ' + str(e))

def system_id():
    try:
        sysid = ovs_vsctl('get', 'Open_vSwitch', '.', 'external_ids:system-id')
        return sysid.strip("\"")
    except Exception:
        logger.error('failed to get system-id')
        return

def is_ovsport_exist(portname):
    name = ovs_get_iface_property(portname, 'name')
    if name is None:
        return False
    return True

def remove_tunnel_by_name(portname):
    if not is_ovsport_exist(portname):
        logger.debug("port %s is not exist, no need to remove it", portname)
        return
    if not delete_ovsport(portname):
        logger.info("cannot delete tunnel port:%s", portname)
        return
    logger.info("delete ovs tunnel port %s", portname)
    return

def get_tunnel_chassis_id(portname):
    chassis = ovs_get_iface_property(portname, 'external_ids:chassis-id')
    if chassis is None:
        return ""
    return chassis

def chassis_ip_to_portname(ip):
    chassis_ip_int = struct.unpack("!L", socket.inet_aton(ip))[0]
    portname = '{}{}'.format(TP_TUNNEL_PORT_NAME_PREFIX, chassis_ip_int)
    return portname

def remove_tunnel_by_ip(ip):
    portname = chassis_ip_to_portname(ip)
    remove_tunnel_by_name(portname)

def create_tunnel(ip, uuid, br = 'br-int'):
    portname = chassis_ip_to_portname(ip)
    if get_tunnel_chassis_id(portname) == uuid:
        logger.info("found a exist tunnel ovsport has "
                    "same chassis-id and portname, skip adding tunnel ovsport")
        return
    remove_tunnel_by_name(portname)
    logger.info("adding ovs tunnel port %s", portname)
    if not create_ovsport(br, portname, 'geneve',
                          {'options:remote_ip':ip,
                           'options:key':'flow',
                           'options:csum':'true',
                           'external_ids:chassis-id':uuid}):
        logger.error("cannot create tunnel port %s", portname)
        return

    return portname

def create_flowbased_tunnel(chassis_id, br = 'br-int'):
    portname = "{}flowbased".format(TP_TUNNEL_PORT_NAME_PREFIX)
    remove_tunnel_by_name(portname)
    logger.info("adding  ovs tunnel port %s", portname)
    if not create_ovsport(br, portname, 'geneve',
                          {'options:key':'flow',
                           'options:remote_ip':'flow',
                           'options:csum':'true',
                           'external_ids:chassis-id':chassis_id}):
        logger.error("cannot create tunnel port %s", portname)
        return

    return portname


def create_patchport(portname, peer_br, br = 'br-int'):
    if not is_br_exist(peer_br):
        logger.info("the bridge %s is not exist, would not create patchports",
                    peer_br)
        return

    peer_portname = portname + "-peer"
    cmd = []
    if not is_ovsport_exist(portname):
        cmd += ['--', 'add-port', br, portname,
                '--', 'set', 'interface', portname, 'type=patch',
                'options:peer={}'.format(peer_portname),
                'external_ids:iface-id={}'.format(portname)
               ]

    if not is_ovsport_exist(peer_portname):
        cmd += ['--', 'add-port', peer_br, peer_portname,
                '--', 'set', 'interface', peer_portname, 'type=patch',
                'options:peer={}'.format(portname),
                'external_ids:iface-id={}'.format(peer_portname)
               ]
    if len(cmd) == 0:
        logger.info("the patchports had been created, skip..")
        return

    try:
        ovs_vsctl(*cmd)
    except Exception as err:
        logger.warning("failed to create patchport, err:%s", err)
    else:
        logger.info("created patchport %s, %s", portname, peer_portname)


def delete_patchport(portname):
    peer_portname = ovs_get_iface_property(portname, 'options:peer')
    if peer_portname is None:
        logger.warning("failed to get %s peer patchport information", portname)
        return

    if not delete_ovsport(portname):
        logger.warning("failed to delete patchport %s", portname)
    else:
        logger.info("removed patchport %s", portname)

    if not is_ovsport_exist(peer_portname):
        logger.warning("patchport %s is not exist", peer_portname)
        return
    if not delete_ovsport(peer_portname):
        logger.warning("failed to delete patchport %s", peer_portname)
    else:
        logger.info("removed patchport %s", peer_portname)

def commit_replaceflows(replace_flows, br = 'br-int'):
    # commit_replaceflows is only consumed by update_logical_view in booting
    # tuplenet stage. Keep all flows in a file to avoid using stdin which may
    # introduce buffer size issue.
    # this function can help to avoid ports' issue of breaking-network, the
    # replace-flows is a transaction, so would not change flows if it hit issue
    filepath = '/tmp/ovs-flow-{}'.format(''.join(
        random.choice(string.ascii_uppercase + string.digits) for _ in range(5)))
    filepath += str(time.time())
    try:
        with open(filepath, 'w') as fp:
            fp.write('\n'.join(replace_flows))
        ovs_ofctl('replace-flows', '-O', OPENFLOW_VER, br, filepath)
        os.remove(filepath)
    except IOError:
        logger.error("failed to write flows into file %s", filepath)
        raise
    except RuntimeError as err:
        logger.error("failed to replace flows in {}".format(filepath))
        raise OVSToolErr("failed to replace flows")
    except OSError as err:
        logger.warning("failed to remove the %s", filepath)
        raise



def commit_flows(add_flows, del_flows):
    # consume batch method to insert/delete flows first.
    # update ovs flow one by one if updateing flow hit issue.

    with flow_lock:
        try:
            total_flow_n = len(del_flows) + len(add_flows)
            if len(del_flows) > 0:
                ovs_ofctl_delflows_batch('br-int', del_flows)
                del_flows = []
            if len(add_flows) > 0:
                ovs_ofctl_addflows_batch('br-int', add_flows)
                add_flows = []
            return total_flow_n
        except Exception as err:
            logger.warn("failed to batch modify flows, "
                        "will try to update one by one, err:%s", err)

        # insert/delete flow one by one once the batch processing hit error
        cm_cnt = 0
        for flow in del_flows:
            try:
                ovs_ofctl('del-flows', 'br-int', flow, '--strict')
            except Exception as err:
                logger.error('failed to delete flow(%s) at ovs, err:%s',
                             flow, err)
                continue;
            cm_cnt += 1

        for flow in add_flows:
            try:
                ovs_ofctl('add-flow', 'br-int', flow)
            except Exception as err:
                logger.error('failed to add flow(%s) at ovs, err:%s',
                             flow, err)
                continue;
            cm_cnt += 1
    return cm_cnt;

def _set_br_failmode(br, mode):
    try:
        cur_mode = ovs_vsctl('get-fail-mode', br)
    except Exception:
        logger.error("failed to get fail mode of bridge %s", br)
        raise OVSToolErr("failed to get fail mode of bridge")
    if cur_mode != mode:
        logger.info("config bridge %s fail-mode into %s", br, mode)
        try:
            ovs_vsctl('set-fail-mode', br, mode)
        except Exception:
            logger.error("failed to config %s fail-mode into %s", br, mode)
            raise OVSToolErr("failed to config fail-mode")

def create_br_int_dsrport(br):
    dsr_port = br + '-dsrgw'
    if is_ovsport_exist(dsr_port) is False:
        if not create_ovsport(br, dsr_port, 'internal'):
            logger.warning("failed to add internal port %s", dsr_port)
            return

    mac = ovs_get_iface_property(dsr_port, 'mac_in_use')
    ofport = ovs_get_iface_property(dsr_port, 'ofport')
    if mac is None or ofport is None:
        logger.error("failed to get bridge %s's mac_in_use or ofport", dsr_port)
        return
    return {'name':dsr_port, 'mac':mac, 'ofport':ofport}

def build_br_integration(br = 'br-int', dsr_port = None):
    if is_br_exist(br):
        logger.info("the bridge %s is exist", br)
        try:
            _set_br_failmode(br, 'secure')
            # if we hit no issue, then it means the bridge is exist
            return br
        except Exception as err:
            logger.info("failed to config %s to secure mode, err:%s", br, err)
            raise OVSToolErr("failed to config secure mode")
        else:
            return br

    try:
        ovs_vsctl('add-br', br, '--', 'set', 'Bridge', br, 'fail-mode=secure')
        logger.info("create bridge %s for integration", br)
    except Exception as err:
        logger.error("failed to create %s, err:%s", br, err)
        raise OVSToolErr("failed to create integration bridge")
    return br

def set_tunnel_tlv(vipclass = 0xffee, br = 'br-int'):
    while True:
        try:
            output = ovs_ofctl('dump-tlv-map', br)
        except Exception as err:
            logger.error("failed dump %s tlv, err:%s", br, err)
            raise OVSToolErr("failed to dump tlv")
        # TODO it is a fool check, update it
        if "tun_metadata0" in output:
            logger.info(output)
            return

        try:
            ovs_ofctl('del-tlv-map', br)
        except Exception as err:
            logger.error("failed to clean %s tlv, err:%s", br, err)
            raise OVSToolErr("failed to clean tlv")

        tlv = "{{class={},type=0,len=8}}->tun_metadata0".format(vipclass)
        try:
            ovs_ofctl('add-tlv-map', br, tlv)
        except Exception as err:
            logger.error('failed to config tlv %s to %s, err:%s', tlv, br, err)
            raise OVSToolErr("failed to config tlv")

        logger.info("set tlv %s on %s", tlv, br)

def set_upcall_rate(br = 'br-int', rate = 100):
    #TODO we need to limite the packet rate of upcalling to packet_controller
    pass


def config_ovsport_bfd(portname, config):
    try:
        ovs_vsctl('set', 'Interface', portname, 'bfd:{}'.format(config))
    except Exception as err:
        logger.info("failed to config %s bfd to %s, "
                    "port may not exist, err:%s",
                    portname, config, err)

def inject_pkt_to_ovsport(cmd_id, packet_data, ofport):
    try:
        ovs_ofctl('packet-out', 'br-int', 'NONE',
                  ('load:{}->NXM_OF_IN_PORT[],'
                  'load:{}->NXM_NX_REG10[16..31],'
                  'load:1->NXM_NX_REG10[1],resubmit(,0)').format(ofport, cmd_id),
                  packet_data)
    except Exception:
        logger.warning("failed to inject packet %s to ofport %d",
                       packet_data, ofport)

