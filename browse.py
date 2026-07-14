import socket
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
import re
import html

class DLNARenderer:
    """Handles UPnP AVTransport devices (Renderers/Players) for controlling playback."""
    NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
    NS_AVT = "urn:schemas-upnp-org:service:AVTransport:1"

    def __init__(self):
        self.desc_url = None
        self.control_url = None
        self.friendly_name = "None"

    @staticmethod
    def discover_renderers(timeout=3):
        """SSDP scan targeting AVTransport services."""
        print("[*] Broadcasting SSDP M-SEARCH for AVTransport Renderers...")
        search_target = DLNARenderer.NS_AVT
        
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
            print(f" [!] SSDP Renderer Socket error: {e}")
        finally:
            sock.close()

        return list(discovered_urls)

    @staticmethod
    def get_friendly_name(desc_url):
        """Fetches the XML description of the renderer to retrieve its friendly name."""
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
        return "Unknown DLNA Renderer"

    def resolve_control_url(self, desc_url):
        """Parses description XML to locate the AVTransport control URL."""
        r = requests.get(desc_url, timeout=5)
        r.raise_for_status()
        
        ns = {'upnp': 'urn:schemas-upnp-org:device-1-0'}
        root = ET.fromstring(r.content)
        
        for service in root.findall('.//upnp:service', ns):
            service_type = service.find('upnp:serviceType', ns).text
            if "AVTransport:1" in service_type:
                control_path = service.find('upnp:controlURL', ns).text
                self.desc_url = desc_url
                self.control_url = urljoin(desc_url, control_path)
                self.friendly_name = self.get_friendly_name(desc_url)
                return self.control_url
                
        raise Exception("AVTransport service not found on this device.")

    def select_renderer(self):
        """Interactive console menu to discover and select a renderer."""
        urls = self.discover_renderers()
        
        if not urls:
            print(" [!] No active DLNA Renderers detected via SSDP.")
            # We return True anyway and fall back to local UI inspection only if they skip
            input("Press Enter to run in browser-only mode (metadata inspector)...")
            return False

        renderers = []
        print("\n[+] Found the following DLNA Renderers:")
        for i, url in enumerate(urls, 1):
            name = self.get_friendly_name(url)
            renderers.append((name, url))
            print(f"  {i}. {name} ({url})")

        while True:
            try:
                choice = input(f"\nSelect a renderer (1-{len(renderers)}) or 'skip': ").strip()
                if choice.lower() == 'skip':
                    return False
                idx = int(choice) - 1
                if 0 <= idx < len(renderers):
                    self.resolve_control_url(renderers[idx][1])
                    print(f"[+] Selected Renderer: {self.friendly_name}")
                    return True
                print("Invalid choice.")
            except ValueError:
                print("Please enter a valid integer or 'skip'.")

    def play(self, uri, title):
        """Tells the renderer to load and play the selected media URI."""
        if not self.control_url:
            print("[!] No active renderer connected to send playback command.")
            return

        print(f"\n[*] Sending track to {self.friendly_name}...")
        print(f"    Title: {title}")

        # Escape characters inside the metadata DIDL block
        safe_title = html.escape(title)
        safe_uri = html.escape(uri)

        # Basic metadata envelope so the player's screen displays the song title
        meta_didl = f"""&lt;DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"&gt;
  &lt;item id="0" parentID="0" restricted="1"&gt;
    &lt;dc:title&gt;{safe_title}&lt;/dc:title&gt;
    &lt;upnp:class&gt;object.item.audioItem.musicTrack&lt;/upnp:class&gt;
    &lt;res&gt;{safe_uri}&lt;/res&gt;
  &lt;/item&gt;
&lt;/DIDL-Lite&gt;"""

        # Step 1: Set URI
        set_uri_soap = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{self.NS_SOAP}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:SetAVTransportURI xmlns:u="{self.NS_AVT}">
      <InstanceID>0</InstanceID>
      <CurrentURI>{uri}</CurrentURI>
      <CurrentURIMetaData>{meta_didl}</CurrentURIMetaData>
    </u:SetAVTransportURI>
  </s:Body>
</s:Envelope>"""

        # Step 2: Fire Play
        play_soap = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{self.NS_SOAP}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:Play xmlns:u="{self.NS_AVT}">
      <InstanceID>0</InstanceID>
      <Speed>1</Speed>
    </u:Play>
  </s:Body>
</s:Envelope>"""

        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "Connection": "close"
        }

        try:
            # Send SetURI Action
            headers["SOAPACTION"] = f'"{self.NS_AVT}#SetAVTransportURI"'
            r1 = requests.post(self.control_url, data=set_uri_soap, headers=headers, timeout=5)
            r1.raise_for_status()

            # Send Play Action
            headers["SOAPACTION"] = f'"{self.NS_AVT}#Play"'
            r2 = requests.post(self.control_url, data=play_soap, headers=headers, timeout=5)
            r2.raise_for_status()
            print(" [✔] Playback command sent successfully!")

        except Exception as e:
            print(f" [!] Playback SOAP request failed: {e}")


class DLNABrowser:
    """Handles parsing the media tree from UPnP/DLNA media servers."""
    DEFAULT_DESC_URL = "http://192.168.132.5:8200/rootDesc.xml"
    NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
    NS_CD = "urn:schemas-upnp-org:service:ContentDirectory:1"

    def __init__(self):
        self.desc_url = None
        self.control_url = None
        
        # Navigation state variables
        self.history = []  
        self.current_id = "0"
        self.current_title = "Root"

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

    def browse_directory(self, object_id):
        """Sends SOAP Browse request and extracts directory listing."""
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
                
        return found_items

    def start_ui(self, renderer_device):
        """Runs the interactive console loop for directory drilling and playback."""
        if not self.control_url:
            print("[!] Cannot start UI. No active server selected.")
            return

        while True:
            # Render Header (displays current server path + selected active playback target)
            path_str = " ➔ ".join([h[1] for h in self.history] + [self.current_title])
            print("\n" + "=" * 60)
            print(f" 📂 PATH: {path_str}")
            print(f" 📺 OUTPUT: {renderer_device.friendly_name}")
            print("=" * 60)

            try:
                items = self.browse_directory(self.current_id)
            except Exception as e:
                print(f" [!] Failed to browse folder: {e}")
                if self.history:
                    print("Returning to previous folder...")
                    self.current_id, self.current_title = self.history.pop()
                    continue
                else:
                    print("Returning to Root...")
                    self.current_id, self.current_title = "0", "Root"
                    continue

            menu_items = []
            option_num = 1

            if self.history:
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
                    # Trigger the renderer action instead of just printing raw info
                    renderer_device.play(item_data['uri'], item_data['title'])
                    input("\nPress Enter to return to browsing...")

            except (ValueError, IndexError):
                print("\n[!] Invalid option. Please try again.")


if __name__ == "__main__":
    renderer = DLNARenderer()
    browser = DLNABrowser()
    
    try:
        # Step 1: Select Active Output (Renderer)
        renderer.select_renderer()
        print("")
        
        # Step 2: Select Library Source (Server)
        if browser.select_server():
            print(f"[+] Active Control URL: {browser.control_url}\n")
            # Step 3: Run User Interface
            browser.start_ui(renderer)
        else:
            print("Exiting.")
            
    except Exception as e:
        print(f"\n[!] Global error: {e}")
