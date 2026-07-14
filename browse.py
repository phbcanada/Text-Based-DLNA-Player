import socket
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
import html
import re

# Fallback location if SSDP discovery finds nothing or fails
DEFAULT_MINIDLNA_DESC_URL = "http://192.168.132.5:8200/rootDesc.xml"

NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_CD = "urn:schemas-upnp-org:service:ContentDirectory:1"

def discover_dlna_servers(timeout=3):
    """Broadcasts SSDP M-SEARCH to discover DLNA Media Servers on the network."""
    print("[*] Broadcasting SSDP M-SEARCH for Content Directory services...")
    search_target = "urn:schemas-upnp-org:service:ContentDirectory:1"
    
    ssdp_request = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        f"ST: {search_target}\r\n"
        f"MX: {timeout}\r\n"
        "\r\n"
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(timeout)
    
    discovered_urls = set()
    try:
        sock.sendto(ssdp_request.encode('utf-8'), ("239.255.255.250", 1900))
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                response = data.decode('utf-8', errors='ignore')
                loc_match = re.search(r'(?i)LOCATION:\s*(http\S+)', response)
                if loc_match:
                    discovered_urls.add(loc_match.group(1))
            except socket.timeout:
                break
    except Exception as e:
        print(f" [!] SSDP Socket error: {e}")
    finally:
        sock.close()

    return list(discovered_urls)


def get_server_friendly_name(desc_url):
    """Quickly fetches a root XML description to find its user-friendly name."""
    try:
        r = requests.get(desc_url, timeout=2)
        r.raise_for_status()
        ns = {'upnp': 'urn:schemas-upnp-org:device-1-0'}
        root = ET.fromstring(r.content)
        
        friendly_name_node = root.find('.//upnp:friendlyName', ns)
        if friendly_name_node is not None:
            return friendly_name_node.text
    except Exception:
        pass
    return "Unknown DLNA Server"


def get_content_directory_url(desc_url):
    """Parses rootDesc.xml to resolve the exact ContentDirectory control URL."""
    print(f"[*] Parsing Description XML at {desc_url}...")
    r = requests.get(desc_url, timeout=5)
    r.raise_for_status()
    
    ns = {'upnp': 'urn:schemas-upnp-org:device-1-0'}
    root = ET.fromstring(r.content)
    
    for service in root.findall('.//upnp:service', ns):
        service_type = service.find('upnp:serviceType', ns).text
        if "ContentDirectory:1" in service_type:
            control_path = service.find('upnp:controlURL', ns).text
            return urljoin(desc_url, control_path)
            
    raise Exception("ContentDirectory service not found in XML description!")


def browse_directory(control_url, object_id="0"):
    """Sends a SOAP Browse request and returns a structured list of files/folders."""
    soap_envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{NS_SOAP}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:Browse xmlns:u="{NS_CD}">
      <ObjectID>{object_id}</ObjectID>
      <BrowseFlag>BrowseDirectChildren</BrowseFlag>
      <Filter>*</Filter>
      <StartingIndex>0</StartingIndex>
      <RequestedCount>100</RequestedCount>  <SortCriteria></SortCriteria>
    </u:Browse>
  </s:Body>
</s:Envelope>"""

    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPACTION": f'"{NS_CD}#Browse"',
        "Connection": "close"
    }

    r = requests.post(control_url, data=soap_envelope, headers=headers, timeout=5)
    r.raise_for_status()
    
    root = ET.fromstring(r.content)
    result_node = root.find(".//Result")
    
    if result_node is None or not result_node.text:
        return []

    didl_xml_str = result_node.text
    found_items = []
    namespaces = {
        'didl': 'urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/',
        'dc': 'http://purl.org/dc/elements/1.1/',
        'upnp': 'urn:schemas-upnp-org:metadata-1-0/upnp/'
    }

    try:
        didl_root = ET.fromstring(didl_xml_str)
        
        # Find child folders
        for container in didl_root.findall('didl:container', namespaces):
            title = container.find('dc:title', namespaces).text
            cid = container.attrib['id']
            found_items.append({'type': 'folder', 'id': cid, 'title': title})
            
        # Find playable tracks
        for item in didl_root.findall('didl:item', namespaces):
            title = item.find('dc:title', namespaces).text
            item_id = item.attrib['id']
            res_node = item.find('didl:res', namespaces)
            if res_node is not None:
                uri = res_node.text
                mime = res_node.attrib.get('protocolInfo', '').split(':')[2]
                found_items.append({'type': 'file', 'id': item_id, 'title': title, 'uri': uri, 'mime': mime})

    except ET.ParseError:
        # Regex Fallback
        containers = re.findall(r'<container id="([^"]+)"[^>]*>.*?<dc:title>([^<]+)</dc:title>', didl_xml_str, re.DOTALL)
        for cid, title in containers:
            found_items.append({'type': 'folder', 'id': cid, 'title': title.strip()})

        items = re.findall(r'<item id="([^"]+)"[^>]*>.*?<dc:title>([^<]+)</dc:title>.*?<res[^>]*>([^<]+)</res>', didl_xml_str, re.DOTALL)
        for item_id, title, uri in items:
            proto_match = re.search(r'protocolInfo="[^"]*:[^"]*:([^"]*):[^"]*"', didl_xml_str)
            mime = proto_match.group(1) if proto_match else "unknown"
            found_items.append({'type': 'file', 'id': item_id, 'title': title.strip(), 'uri': uri.strip(), 'mime': mime})
            
    return found_items


def select_server():
    """SSDP scan with a fallback option and interactive selection menu."""
    urls = discover_dlna_servers()
    
    if not urls:
        print(" [!] No active DLNA servers detected via SSDP.")
        use_fallback = input(f"Would you like to try connecting to default IP ({DEFAULT_MINIDLNA_DESC_URL})? (y/n): ").strip().lower()
        if use_fallback == 'y':
            return DEFAULT_MINIDLNA_DESC_URL
        return None

    servers = []
    print("\n[+] Found the following DLNA Servers:")
    for i, url in enumerate(urls, 1):
        name = get_server_friendly_name(url)
        servers.append((name, url))
        print(f"  {i}. {name} ({url})")

    if len(servers) == 1:
        print(f"\n[*] Only one server found. Automatically selecting: {servers[0][0]}")
        return servers[0][1]

    while True:
        try:
            choice = input(f"\nSelect a server (1-{len(servers)}) or type 'exit': ").strip()
            if choice.lower() == 'exit':
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(servers):
                return servers[idx][1]
            print("Invalid choice.")
        except ValueError:
            print("Please enter a valid integer.")


def run_interactive_browser(control_url):
    """Runs a loop allowing the user to drill down and navigate up directories."""
    history = []  # List of tuples: (folder_id, folder_title)
    current_id = "0"
    current_title = "Root"

    while True:
        # Draw Breadcrumb Path Header
        path_str = " ➔ ".join([h[1] for h in history] + [current_title])
        print("\n" + "=" * 60)
        print(f" 📂 PATH: {path_str}")
        print("=" * 60)

        # Fetch contents of current directory
        try:
            items = browse_directory(control_url, current_id)
        except Exception as e:
            print(f" [!] Failed to browse folder: {e}")
            if history:
                print("Returning to previous folder...")
                current_id, current_title = history.pop()
                continue
            else:
                print("Returning to Root...")
                current_id, current_title = "0", "Root"
                continue

        # Prepare Interactive Menu Options
        menu_items = []
        option_num = 1

        # Add Back Option if we are deep in the tree
        if history:
            print(f"  0. ◀ [..] Go Back up a level")
            menu_items.append(('back', None))

        if not items:
            print("     [Folder is empty]")
        else:
            for item in items:
                icon = "📁" if item['type'] == 'folder' else "🎵"
                print(f"  {option_num}. {icon} {item['title']}")
                menu_items.append((item['type'], item))
                option_num += 1

        print("  q. 🛑 Quit Browser")
        print("-" * 60)

        choice = input("Select an option (number or 'q'): ").strip().lower()

        if choice == 'q':
            print("\nExiting browser. Goodbye!")
            break

        try:
            idx = int(choice)
            
            # Route back navigation
            if history and idx == 0:
                current_id, current_title = history.pop()
                continue
                
            # If history is present, option '0' was 'back'. 
            # Our menu_items index aligns with selection index directly if 'back' is element 0.
            # If history is empty, menu index starts from 1, so index is input - 1.
            selected_idx = idx if history else idx - 1
            
            if selected_idx < 0 or selected_idx >= len(menu_items):
                print("\n[!] Invalid index selection. Try again.")
                continue

            item_type, item_data = menu_items[selected_idx]

            if item_type == 'folder':
                # Push the current directory details to history before entering new directory
                history.append((current_id, current_title))
                current_id = item_data['id']
                current_title = item_data['title']
                
            elif item_type == 'file':
                # Display detailed file metadata
                print("\n" + "★" * 50)
                print(f"  Title: {item_data['title']}")
                print(f"  URI:   {item_data['uri']}")
                print(f"  Mime:  {item_data['mime']}")
                print("★" * 50)
                input("\nPress Enter to return to browsing...")

        except (ValueError, IndexError):
            print("\n[!] Invalid choice. Please select a valid option from the menu.")


if __name__ == "__main__":
    try:
        target_desc_url = select_server()
        if not target_desc_url:
            print("Exiting.")
            exit(0)
            
        ctrl_url = get_content_directory_url(target_desc_url)
        print(f"[+] Active Control URL: {ctrl_url}\n")
        
        # Launch the custom UI loop
        run_interactive_browser(ctrl_url)
            
    except Exception as e:
        print(f"\n[!] Global error: {e}")
