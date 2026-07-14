import socket
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
import re

class DLNABrowser:
    """Handles parsing the media tree from UPnP/DLNA media servers."""
    DEFAULT_DESC_URL = "http://192.168.132.5:8200/rootDesc.xml"
    NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
    NS_CD = "urn:schemas-upnp-org:service:ContentDirectory:1"

    def __init__(self):
        self.desc_url = None
        self.control_url = None
        self.history = []  
        self.current_id = "0"
        self.current_title = "Root"
        self.cache = {}

    @staticmethod
    def discover_servers(timeout=3):
        """SSDP scan targeting ContentDirectory services."""
        print("[*] Broadcasting SSDP M-SEARCH for Content Directory services...")
        search_target = DLNABrowser.NS_CD
        
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
            print(f" [!] SSDP Server Socket error: {e}")
        finally:
            sock.close()

        return list(discovered_urls)

    @staticmethod
    def get_friendly_name(desc_url):
        """Fetches description XML and extracts friendlyName."""
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

    def resolve_control_url(self, desc_url):
        """Parses description XML to locate ContentDirectory control URL."""
        print(f"[*] Parsing Description XML at {desc_url}...")
        r = requests.get(desc_url, timeout=5)
        r.raise_for_status()
        
        ns = {'upnp': 'urn:schemas-upnp-org:device-1-0'}
        root = ET.fromstring(r.content)
        
        for service in root.findall('.//upnp:service', ns):
            service_type = service.find('upnp:serviceType', ns).text
            if "ContentDirectory:1" in service_type:
                control_path = service.find('upnp:controlURL', ns).text
                self.desc_url = desc_url
                self.control_url = urljoin(desc_url, control_path)
                return self.control_url
                
        raise Exception("ContentDirectory service not found in XML description!")

    def select_server(self):
        """Interactive console menu to discover and select a media server."""
        urls = self.discover_servers()
        
        if not urls:
            print(" [!] No active DLNA servers detected via SSDP.")
            use_fallback = input(f"Would you like to try the default IP ({self.DEFAULT_DESC_URL})? (y/n): ").strip().lower()
            if use_fallback == 'y':
                self.resolve_control_url(self.DEFAULT_DESC_URL)
                return True
            return False

        servers = []
        print("\n[+] Found the following DLNA Servers:")
        for i, url in enumerate(urls, 1):
            name = self.get_friendly_name(url)
            servers.append((name, url))
            print(f"  {i}. {name} ({url})")

        if len(servers) == 1:
            print(f"\n[*] Only one server found. Automatically selecting: {servers[0][0]}")
            self.resolve_control_url(servers[0][1])
            return True

        while True:
            try:
                choice = input(f"\nSelect a server (1-{len(servers)}) or type 'exit': ").strip()
                if choice.lower() == 'exit':
                    return False
                idx = int(choice) - 1
                if 0 <= idx < len(servers):
                    self.resolve_control_url(servers[idx][1])
                    return True
                print("Invalid choice.")
            except ValueError:
                print("Please enter a valid integer.")

    def browse_directory(self, object_id, force_refresh=False):
        """Sends SOAP Browse request and extracts directory listing (with caching)."""
        if not force_refresh and object_id in self.cache:
            return self.cache[object_id]

        soap_envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{self.NS_SOAP}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:Browse xmlns:u="{self.NS_CD}">
      <ObjectID>{object_id}</ObjectID>
      <BrowseFlag>BrowseDirectChildren</BrowseFlag>
      <Filter>*</Filter>
      <StartingIndex>0</StartingIndex>
      <RequestedCount>999</RequestedCount>
      <SortCriteria></SortCriteria>
    </u:Browse>
  </s:Body>
</s:Envelope>"""

        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{self.NS_CD}#Browse"',
            "Connection": "close"
        }

        r = requests.post(self.control_url, data=soap_envelope, headers=headers, timeout=5)
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
            
            for container in didl_root.findall('didl:container', namespaces):
                title = container.find('dc:title', namespaces).text
                cid = container.attrib['id']
                found_items.append({'type': 'folder', 'id': cid, 'title': title})
                
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
                
        self.cache[object_id] = found_items
        return found_items

    def _parse_queue_selection(self, args_str, max_val, index_offset):
        """Parses a user input selection string (e.g., '1 2 5-8') into list indices."""
        indices = []
        # Match standalone integers or ranges like "5-8"
        tokens = re.findall(r'(\d+)-(\d+)|(\d+)', args_str)
        
        for range_start, range_end, single in tokens:
            if single:
                val = int(single) - index_offset
                if 0 <= val < max_val:
                    indices.append(val)
            elif range_start and range_end:
                start = int(range_start) - index_offset
                end = int(range_end) - index_offset
                # Ensure correct ordering for range expansion
                if start > end:
                    start, end = end, start
                for val in range(start, end + 1):
                    if 0 <= val < max_val:
                        indices.append(val)
        return indices

    def start_ui(self, play_queue):
        """Runs the interactive console loop for directory drilling and queuing."""
        if not self.control_url:
            print("[!] Cannot start UI. No active server selected.")
            return

        while True:
            path_str = " ➔ ".join([h[1] for h in self.history] + [self.current_title])
            print("\n" + "=" * 60)
            print(f" 📂 BROWSER: {path_str}")
            print("=" * 60)

            try:
                items = self.browse_directory(self.current_id)
            except Exception as e:
                print(f" [!] Failed to browse folder: {e}")
                if self.history:
                    self.current_id, self.current_title = self.history.pop()
                    continue
                else:
                    self.current_id, self.current_title = "0", "Root"
                    continue

            menu_items = []
            option_num = 1
            has_files = False

            if self.history:
                print(f"  0. ◀ [..] Go Back up a level")
                menu_items.append(('back', None))

            if not items:
                print("     [Folder is empty]")
            else:
                for item in items:
                    icon = "📁" if item['type'] == 'folder' else "🎵"
                    if item['type'] == 'file':
                        has_files = True
                    print(f"  {option_num}. {icon} {item['title']}")
                    menu_items.append((item['type'], item))
                    option_num += 1

            # Build terminal instructions dynamically based on directory content
            print("-" * 60)
            if has_files:
                print("  q      : Queue all playable files in this folder")
                print("  q [ids]: Queue specified entries (e.g., 'q 1 3 5-8')")
            print("  b      : Return to Main Menu")
            print("-" * 60)

            raw_choice = input("Select an option: ").strip()
            choice = raw_choice.lower()

            if choice == 'b':
                break

            # Check if the user is triggering the batch queue 'q' command
            if has_files and (choice == 'q' or choice.startswith('q ')):
                args = raw_choice[1:].strip()
                index_offset = 0 if self.history else 1
                
                # Case 1: Simple 'q' queues everything that is a file
                if not args:
                    queued_count = 0
                    for item_type, item_data in menu_items:
                        if item_type == 'file':
                            play_queue.add_to_queue(item_data)
                            queued_count += 1
                    print(f"\n[+] Enqueued {queued_count} files.")
                    continue
                
                # Case 2: 'q 1 2 5-8' queues specific indexes
                selected_indices = self._parse_queue_selection(args, len(menu_items), index_offset)
                if not selected_indices:
                    print("\n[!] No valid files matched your selection query.")
                    continue
                
                queued_count = 0
                for idx in selected_indices:
                    item_type, item_data = menu_items[idx]
                    if item_type == 'file':
                        play_queue.add_to_queue(item_data)
                        queued_count += 1
                    else:
                        print(f" [!] Selection {idx + index_offset} is a directory; skipped.")
                
                print(f"\n[+] Enqueued {queued_count} files.")
                continue

            try:
                idx = int(choice)
                if self.history and idx == 0:
                    self.current_id, self.current_title = self.history.pop()
                    continue
                    
                selected_idx = idx if self.history else idx - 1
                if selected_idx < 0 or selected_idx >= len(menu_items):
                    print("\n[!] Invalid selection.")
                    continue

                item_type, item_data = menu_items[selected_idx]

                if item_type == 'folder':
                    self.history.append((self.current_id, self.current_title))
                    self.current_id = item_data['id']
                    self.current_title = item_data['title']
                    
                elif item_type == 'file':
                    play_queue.add_to_queue(item_data)

            except (ValueError, IndexError):
                print("\n[!] Invalid option. Please try again.")
