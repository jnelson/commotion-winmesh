#!/usr/bin/env python

# Commotion Winmesh was developed by Scal.io (http://scal.io) 
# with the generous support of Open Technology Institute
# (http://oti.newamerica.net/).
#
# Josh Steiner (https://github.com/vitriolix/)
# Jonathan Nelson (https://github.com/jnelson/)

import os
import io
import sys
import socket
import re
import inspect
import pickle
import subprocess  # for netsh and olsrd
import time
try:  # Windows-specific
    import wmi  # http://timgolden.me.uk/python/wmi/index.html
    from external.PyWiWi import WindowsWifi
    from external.PyWiWi import WindowsNativeWifiApi as PWWnw
    WMI = wmi.WMI()
except:
    pass


newline = "\r\n"

commotion_BSSID_re = re.compile(r'[01]2:CA:FF:EE:BA:BE')
commotion_default_SSID = 'commotionwireless.net'

profile_extension = ".xml"
olsrd_conf_extension = ".olsrd.conf"

dot11_to_wlan = {
        "dot11_BSS_type_infrastructure": "ESS",
        "dot11_BSS_type_independent": "IBSS",
        "DOT11_AUTH_ALGO_80211_OPEN": "open",
        "DOT11_AUTH_ALGO_80211_SHARED_KEY": "shared",
        "DOT11_AUTH_ALGO_WPA": "WPA",
        "DOT11_AUTH_ALGO_WPA_PSK": "WPAPSK",
        "DOT11_AUTH_ALGO_RSNA": "WPA2",
        "DOT11_AUTH_ALGO_RSNA_PSK": "WPA2PSK",
        "DOT11_CIPHER_ALGO_NONE": "none",
        "DOT11_CIPHER_ALGO_WEP40": "WEP",
        "DOT11_CIPHER_ALGO_WEP104": "WEP",
        "DOT11_CIPHER_ALGO_WEP": "WEP",
        "DOT11_CIPHER_ALGO_TKIP": "TKIP",
        "DOT11_CIPHER_ALGO_CCMP": "AES"
        }


def get_own_path(extends_with=None):
    if extends_with:
        sep = "/"
    else:
        extends_with = ""
        sep = ""
    base_path = os.path.dirname(os.path.abspath(sys.argv[0]))
    #base_path = os.path.dirname(os.path.abspath(inspect.getfile(
    #        inspect.currentframe())))
    ext_path = os.path.abspath("".join([base_path, sep, extends_with]))
    return ext_path

OLSRD_PATH = "olsrd"
profile_template_path = get_own_path("templates/profile_template.xml.py")
profile_key_template_path = get_own_path("templates/sharedKey.xml.py")
prev_profile_path = get_own_path(".prevprofile")
olsrd_exe_path = get_own_path(os.path.join(OLSRD_PATH, "olsrd.exe"))
olsrd_conf_path = get_own_path(os.path.join(OLSRD_PATH, "olsrd.conf"))
olsrd_conf_template_path = get_own_path("templates/olsrd.conf.py")


def write_file(path, filestring):
    with io.open(path, mode="w", newline=newline) as f:
        f.write(unicode(filestring))


def load_file(path):
    with io.open(path, mode="rt", newline=newline) as f:
        return "".join(line.rstrip() for line in f)


def apply_template(template_string, params):
    return template_string.format(**params)


def template_file_to_string(template_path, params):
    template = load_file(template_path)
    return apply_template(template, params)


def create_file_from_template(template_path, result_path, params):
    applied_template = template_file_to_string(template_path, params)
    write_file(result_path, applied_template)


def make_wlan_profile(netsh_spec):
    xml_path = get_own_path("".join([netsh_spec["profile_name"],
                                            profile_extension]))
    create_file_from_template(profile_template_path, xml_path, netsh_spec)
    return xml_path


def make_olsrd_conf(iface_name, profile):
    conf_path = get_own_path("".join([profile["ssid"], olsrd_conf_extension]))
    masked_ip = []
    for ip, mask in zip(profile["ip"].split("."),
                        profile["netmask"].split(".")):
        masked_ip.append(str(int(ip) & int(mask)))
    params = {"masked_ip": '.'.join(masked_ip),
              "netmask": profile["netmask"],
              "interface_name": iface_name}
    create_file_from_template(olsrd_conf_template_path, conf_path, params)
    return conf_path


def wlan_dot11bssid_to_string(dot11Bssid):
    return ":".join(map(lambda x: "%02X" % x, dot11Bssid))


def get_wlan_interface_state(PyWiWi_iface):
    s, S = WindowsWifi.queryInterface(PyWiWi_iface, 'interface_state')
    return s, S


def get_wlan_profile_xml(PyWiWi_iface, profile_name):
    return WindowsWifi.getWirelessProfileXML(PyWiWi_iface, profile_name)


def get_wlan_current_connection(PyWiWi_iface):
    ''' Returns connection attributes if connected, None if not. '''
    iface_state = get_wlan_interface_state(PyWiWi_iface)[1]
    print "current iface state", iface_state
    if iface_state == "wlan_interface_state_connected":
        cnx, CNX = WindowsWifi.queryInterface(PyWiWi_iface,
                                              'current_connection')
    else:
        cnx, CNX = None, None
    return cnx, CNX


def get_current_connection(PyWiWi_iface):
    ''' Returns digested connection attributes if connected, None if not. '''
    cnx, CNX = get_wlan_current_connection(PyWiWi_iface)
    if CNX:
        CNXaa = CNX["wlanAssociationAttributes"]
        result = {"profile_name": CNX["strProfileName"],
                  "bssid": CNXaa["dot11Bssid"],
                  "mode": CNX["wlanConnectionMode"],
                  "dot11_bss_type": CNXaa["dot11BssType"],
                  "phy_type": CNXaa["dot11PhyType"],
                  "ssid": CNXaa["dot11Ssid"]}
    else:
        result = CNX
    return result


def collect_interfaces():
    ifaces = WindowsWifi.getWirelessInterfaces()
    wmi_ifaces = wmi.WMI().Win32_NetworkAdapter()
    ifaces_by_guid = {}
    for wmi_iface in wmi_ifaces:
        ifaces_by_guid[wmi_iface.GUID] = wmi_iface
    for iface in ifaces:
        wmi_iface = ifaces_by_guid[iface.guid_string]
        wmi_iface_conf = wmi.WMI().Win32_NetworkAdapterConfiguration(
                InterfaceIndex=wmi_iface.InterfaceIndex)[0]
        # functions needed to restore initial state
        iface.EnableDHCP = wmi_iface_conf.EnableDHCP
        iface.EnableStatic = wmi_iface_conf.EnableStatic
        iface.SetGateways = wmi_iface_conf.SetGateways
        # preserve initial state
        iface.initial_connection = get_current_connection(iface)
        iface.netsh_name = wmi_iface.NetConnectionID
        iface.MAC = wmi_iface.MACAddress
        iface.IPs = wmi_iface_conf.IPAddress
        iface.subnet_masks = wmi_iface_conf.IPSubnet
        iface.gateways = wmi_iface_conf.DefaultIPGateway
        iface.DHCP_enabled = wmi_iface_conf.DHCPEnabled
    return ifaces


def get_interface_by_guid(guid):
    ifaces = collect_interfaces()
    for iface in ifaces:
        if iface.guid_string == guid:
            return iface
    raise Exception("Requested network interface not present")


# collect existing networks on wireless interfaces
def collect_networks():
    # collect networks and useful metadata
    def is_commotion_in_bssList(bssList):
        for bss in bssList:
            if commotion_BSSID_re.match(bss.bssid):
                return True
        return False
    ifaces = collect_interfaces()
    nets = []
    nets_dict = {}
    for iface in ifaces:
        # SSID<one-many>BSSID
        # WW.gWNBL gives BSSIDs with SSID each
        nets_bss = WindowsWifi.getWirelessNetworkBssList(iface)
        # WW.gWANL gives SSIDs with BSSID count and sec info
        nets_avail = WindowsWifi.getWirelessAvailableNetworkList(iface)
        # need SSID and sec info to construct profile
        # need SSID, profile, and preferred BSSIDs for WW.connect()
        for net_avail in nets_avail:
            net = {"interface": iface,
                   "auth": net_avail.auth,
                   "cipher": net_avail.cipher,
                   "bss_list": [],
                   "commotion": False}
            for bss in nets_bss:
                if bss.ssid == net_avail.ssid:
                    net["bss_list"].append(bss)
                    if not net["commotion"]:
                        # one commotion BSSID marks the SSID as commotion
                        net["commotion"] = bool(
                                commotion_BSSID_re.match(bss.bssid))
                    nets_dict[(iface.netsh_name, bss.ssid, bss.bssid)] = {
                            "interface": iface,
                            "ssid": bss.ssid,
                            "bssid": bss.bssid,
                            "dot11_bss_type": bss.bss_type,
                            "bss_type": dot11_to_wlan[bss.bss_type],
                            "auth": net_avail.auth,
                            "cipher": net_avail.cipher,
                            "quality": bss.link_quality
                            }
            nets.append(net)
    return nets, ifaces, nets_dict


def find_matching_available_nets(ssid, bssid):
    if (nets_dict is not None):        
        return [n for n in nets_dict if (n[1] == ssid and n[2] == bssid)]


def netsh_add_profile_cmd(path):
    return "".join(["netsh wlan add profile",
                    " filename=\"",
                    path,
                    profile_extension,
                    "\""])


def start_olsrd_cmd(iface_name, olsrd_conf):
    print "olsrd_exe_path", olsrd_exe_path
    return "".join([olsrd_exe_path,
                    #" -d 2",
                    " -i \"",
                    iface_name,
                    "\"",
                    " -f \"",
                    olsrd_conf,
                    "\""])


def netsh_disconnect_cmd(netsh_spec):
    return "".join(["netsh wlan disconnect",
                    " interface=\"",
                    netsh_spec["iface_name"],
                    "\""])


def netsh_add_profile(path):
    cmd =  netsh_add_profile_cmd(get_own_path(path))
    print cmd
    add = subprocess.Popen(cmd,
                           stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    return add.wait()


def start_olsrd(iface_name, olsrd_conf=olsrd_conf_path):
    olsrd = subprocess.Popen(start_olsrd_cmd(iface_name, olsrd_conf),
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    return olsrd


def save_rollback_params(iface, mesh_net):
    fname = prev_profile_path
    connectable = iface.initial_connection
    if connectable:
        print "connectable mode", connectable["mode"]
        if connectable["mode"] == "wlan_connection_mode_profile":
            connectable["restore"] = True
        elif connectable["mode"] == "wlan_connection_mode_temporary_profile":
            connectable["restore"] = True
        elif connectable["mode"] == "wlan_connection_mode_auto":
            # will reconnect itself but maybe not with good IP settings
            connectable["restore"] = True
        else:
            # "wlan_connection_mode_discovery_secure"
            # "wlan_connection_mode_discovery_unsecure"
            connectable["restore"] = False
    else:
        connectable = {
                "restore": False
                }
    connectable["delete_mesh_after_restore"] = True
    connectable["mesh_wlan_name"] = mesh_net["ssid"]
    connectable["interface"] = {
            "guid": iface.guid_string,
            "DHCP_enabled": iface.DHCP_enabled,
            "IPs": iface.IPs,
            "subnet_masks": iface.subnet_masks,
            "gateways": iface.gateways
            }
    pickle.dump(connectable, open(fname, "w"))
    print "saved restore file at", fname


def wlan_connect(iface, spec):
    # PyWiWi.WindowsWifi.connect() only works reliably in profile mode.
    #   So we use that. We need it because the netsh wlan connect doesn't
    #   allow BSSID specification.
    cnxp = {"connectionMode": "wlan_connection_mode_profile",
            "profile": spec["profile_name"],
            "ssid": spec["ssid"],
            "bssidList": [spec["bssid"]],
            "bssType": spec["dot11_bss_type"],
            "flags": 0}
    result = WindowsWifi.connect(iface, cnxp)
    print "connecting to", spec["profile_name"], "; result:", result


def netsh_set_ip_cmd(netsh_name,
                     enable_DHCP,
                     ip=None,
                     subnet_mask=None):
    if enable_DHCP == False:
        source = "static"
        address = "".join([" ",
                "addr=", ip,
                "mask=", subnet_mask,
                "gateway=none"])  # set gateways via WMI
    else:
        source = "dhcp"
        address = ""
    return "".join(["netsh interface ip set address",
                    " name=\"",
                    netsh_name,
                    "\"",
                    " source=",
                    source,
                    address])


def netsh_set_ip(iface, enable_DHCP, ip=None, subnet_mask=None):
    print "setting [DHCP active] [IP] [subnet mask]", enable_DHCP, ip, subnet_mask
    if not enable_DHCP:
        iface.EnableStatic([ip], [subnet_mask])
    else:
        res = iface.EnableDHCP()
        print "result of enabling DHCP", res


def set_ip(iface, enable_DHCP, IPs=None, subnet_masks=None, gateways=None):
    if enable_DHCP == True:
        success = netsh_set_ip(iface, enable_DHCP)
    else:
        success = netsh_set_ip(iface, enable_DHCP, IPs[0], subnet_masks[0])
    if gateways:
        gw = iface.SetGateways(DefaultIPGateway=gateways,
                GatewayCostMetric=[1]*len(gateways))  #TODO?: bug for someone


def make_network(iface, netsh_spec, profile):
    make_wlan_profile(netsh_spec)
    netsh_add_profile(netsh_spec["ssid"])
    set_ip(iface, enable_DHCP=False,
                  IPs=[profile["ip"]],
                  subnet_masks=[profile["netmask"]],
                  gateways=None)
    olsrd_conf = make_olsrd_conf(iface.netsh_name, profile)
    wlan_connect(iface, netsh_spec)
    olsrd = start_olsrd(netsh_spec["interface"].netsh_name, olsrd_conf)
    return olsrd


def netsh_delete_profile_cmd(wlan_profile_name, interface_name):
    return "".join(["netsh wlan delete profile",
                    " name=\"",
                    wlan_profile_name,
                    "\"",
                    " interface=\"",
                    interface_name,
                    "\""])

def netsh_delete_profile(wlan_profile_name, interface_name):
    p = subprocess.Popen(netsh_delete_profile_cmd(wlan_profile_name,
                                                  interface_name),
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    return p.wait()


def apply_rollback_params():
    fname = prev_profile_path
    if os.path.isfile(fname):
        print "restoring from", fname
        connectable = pickle.load(open(fname, "r"))
        iface = get_interface_by_guid(connectable["interface"]["guid"])
        if connectable["restore"]:
            if connectable["mode"] == "wlan_connection_mode_auto":
                connectable["mode"] = "wlan_connection_mode_profile"
            wlan_connect(iface, connectable)
            time.sleep(3)
            if connectable["interface"]["DHCP_enabled"]:
                set_ip(iface, enable_DHCP=True)
            else:
                set_ip(iface,
                       enable_DHCP=False,
                       IPs=connectable["interface"]["IPs"],
                       subnet_masks=connectable["interface"]["subnet_masks"],
                       gateways=connectable["interface"]["gateways"])
        else:
            print "restore not requested"
    else:
        print "No restore file found"
    # delete wlan profile store entry for current mesh
    if "connectable" in locals() and "iface" in locals():
        netsh_delete_profile(connectable["mesh_wlan_name"],
                             iface.netsh_name)


def print_available_networks():
    global net_list
    if net_list is None:
        refresh_net_list()
    print "#   @ CW? Interface     Qual BSSID             SSID"
    for idx, net in enumerate(net_list):
        is_current = net["interface"].initial_bssid == net["bss_list"][0].bssid
        print "".join(["{0:>2} ",
                       "{4:^3.1}",
                       "{3:^3.1} ",
                       "{1.description:13.13} ",
                       "{2.link_quality:>3}% ",
                       "{2.bssid} ",
                       "{2.ssid}"]).format(idx + 1,
                                           net["interface"],
                                           net["bss_list"][0],
                                           str(net["commotion"]),
                                           str(is_current))


def cli_choose_network():
    print_available_networks()
    return int(raw_input("".join(["Enter the # of the network to join,\n",
                                  "enter 0 (zero) to start a new network,\n",
                                  "or enter Q to quit:\n"])))


def make_netsh_spec(net):
    wlan = dot11_to_wlan
    netsh_spec = {
            "interface": net["interface"],
            "iface_name": net["interface"].netsh_name,
            "MAC": net["interface"].MAC,
            "profile_name": net["ssid"],
            "ssid_hex": net["ssid"].encode('hex').upper(),
            "ssid": net["ssid"],
            "bssid": net["bssid"],
            "dot11_bss_type": net["dot11_bss_type"],
            "bss_type": wlan[net["dot11_bss_type"]],
            "auth": wlan[net["auth"]],
            "cipher": wlan[net["cipher"]],
            "key_material": net.get("key_material", None),
            "key_type": ("keyType" if \
                    wlan[net["auth"]] == "WEP" else "passPhrase")
            }
    if netsh_spec["key_material"] is not None:
        netsh_spec["shared_key"] = template_file_to_string(
                profile_key_template_path,
                netsh_spec)
    else:
        netsh_spec["shared_key"] = ""
    return netsh_spec


def cli_choose_iface(ifaces):
    print "#   Interface"
    idx = 0
    for iface in ifaces:
        print "".join(["{0:>2} ",
                       "{1.description}"]).format(idx + 1, iface)
        idx = 1 + idx
    iface_choice = raw_input("Enter the # of the interface to use:\n")
    return ifaces[int(iface_choice) - 1]


net_list = None
iface_list = None
nets_dict = None

def refresh_net_list():
    global net_list
    global iface_list
    global nets_dict
    net_list, iface_list, nets_dict = collect_networks()
    net_list.sort(key=lambda opt: opt["bss_list"][0].link_quality, reverse=True)


def connect_or_start_profiled_mesh(profile):
    print "selected mesh", profile["ssid"]
    #FIXME: Until interface selection in UI, just use first available
    if len(profile["available_nets"]) > 0:
        print "connecting to existing mesh"
        target_net = nets_dict[profile["available_nets"][0]]  # hack
        target_iface = target_net["interface"]
        save_rollback_params(target_iface, profile)
        target_net["key_material"] = profile.get("psk", None)
        netsh_spec = make_netsh_spec(target_net)
    else:
        print "creating new mesh", profile["ssid"]
        target_iface = iface_list[0]  # hack
        dummy_net = {
                "interface": target_iface,
                "profile_name": profile["ssid"],
                "ssid": profile["ssid"],
                "bssid": profile["bssid"],
                "dot11_bss_type": "dot11_BSS_type_independent",
                "bss_type": "IBSS",
                "auth": "DOT11_AUTH_ALGO_RSNA_PSK",  #WPA2PSK
                "cipher": "DOT11_CIPHER_ALGO_CCMP",  #AES
                "key_material": profile.get("psk", None)
                }
        save_rollback_params(target_iface, dummy_net)
        netsh_spec = make_netsh_spec(dummy_net)
        netsh_spec["iface_name"] = target_iface.netsh_name
    olsrd = make_network(target_iface, netsh_spec, profile)
    return olsrd


