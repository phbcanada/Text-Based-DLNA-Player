import socket
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
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

    @property
    def host(self):
        """Extracts and returns the hostname or IP address of the renderer."""
        if self.desc_url:
            return urlparse(self.desc_url).hostname
        return "Unknown"

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
                name = friendly_name_node.text
                
                # Check for and substitute unresolved $(hostname) strings
                if name and "$(hostname)" in name:
                    actual_host = urlparse(desc_url).hostname or "Unknown"
                    name = name.replace("$(hostname)", actual_host)
                return name
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
                choice = input(f"\nSelect a renderer (1-{len(renderers)}) or type 'skip': ").strip()
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

    def play_uri(self, uri, title):
        """Tells the renderer to load and play the selected media URI."""
        if not self.control_url:
            return False

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

        play_soap = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{self.NS_SOAP}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:Play xmlns:u="{self.NS_AVT}">
      <InstanceID>0</InstanceID>
      <Speed>1</Speed>
    </u:Play>
  </s:Body>
</s:Envelope>"""

        headers = {"Content-Type": 'text/xml; charset="utf-8"', "Connection": "close"}
        try:
            headers["SOAPACTION"] = f'"{self.NS_AVT}#SetAVTransportURI"'
            r1 = requests.post(self.control_url, data=set_uri_soap, headers=headers, timeout=5)
            r1.raise_for_status()

            headers["SOAPACTION"] = f'"{self.NS_AVT}#Play"'
            r2 = requests.post(self.control_url, data=play_soap, headers=headers, timeout=5)
            r2.raise_for_status()
            return True
        except Exception as e:
            print(f" [!] Playback action failed: {e}")
            return False

    def pause(self):
        """Sends Pause command to renderer."""
        if not self.control_url: return
        soap = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{self.NS_SOAP}"><s:Body><u:Pause xmlns:u="{self.NS_AVT}"><InstanceID>0</InstanceID></u:Pause></s:Body></s:Envelope>"""
        self._send_command("Pause", soap)

    def stop(self):
        """Sends Stop command to renderer."""
        if not self.control_url: return
        soap = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{self.NS_SOAP}"><s:Body><u:Stop xmlns:u="{self.NS_AVT}"><InstanceID>0</InstanceID></u:Stop></s:Body></s:Envelope>"""
        self._send_command("Stop", soap)

    def resume(self):
        """Sends Play command to resume paused playback."""
        if not self.control_url: return
        soap = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{self.NS_SOAP}"><s:Body><u:Play xmlns:u="{self.NS_AVT}"><InstanceID>0</InstanceID><Speed>1</Speed></u:Play></s:Body></s:Envelope>"""
        self._send_command("Play", soap)

    def get_transport_state(self):
        """Queries current state (PLAYING, STOPPED, PAUSED_PLAYBACK, etc.)."""
        if not self.control_url:
            return "NO_MEDIA_PRESENT"
        soap = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{self.NS_SOAP}">
  <s:Body>
    <u:GetTransportInfo xmlns:u="{self.NS_AVT}">
      <InstanceID>0</InstanceID>
    </u:GetTransportInfo>
  </s:Body>
</s:Envelope>"""
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{self.NS_AVT}#GetTransportInfo"',
            "Connection": "close"
        }
        try:
            r = requests.post(self.control_url, data=soap, headers=headers, timeout=2)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            state_node = root.find(".//CurrentTransportState")
            if state_node is not None:
                return state_node.text
        except Exception:
            pass
        return "UNKNOWN"

    def get_position_info(self):
        """Queries the renderer for current track duration, position, and metadata."""
        if not self.control_url:
            return {"title": "None", "duration": "00:00:00", "position": "00:00:00"}
        
        soap = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="{self.NS_SOAP}">
  <s:Body>
    <u:GetPositionInfo xmlns:u="{self.NS_AVT}">
      <InstanceID>0</InstanceID>
    </u:GetPositionInfo>
  </s:Body>
</s:Envelope>"""
        
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{self.NS_AVT}#GetPositionInfo"',
            "Connection": "close"
        }
        
        try:
            r = requests.post(self.control_url, data=soap, headers=headers, timeout=2)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            
            duration = root.find(".//TrackDuration").text or "00:00:00"
            position = root.find(".//RelTime").text or "00:00:00"
            
            title = "Unknown"
            meta_xml = root.find(".//TrackMetaData").text
            if meta_xml and meta_xml != "NOT_IMPLEMENTED":
                try:
                    meta_root = ET.fromstring(meta_xml)
                    ns = {'dc': 'http://purl.org/dc/elements/1.1/'}
                    title_node = meta_root.find(".//dc:title", ns)
                    if title_node is not None:
                        title = title_node.text
                except Exception:
                    pass
                    
            return {"title": title, "duration": duration, "position": position}
        except Exception:
            return {"title": "None", "duration": "00:00:00", "position": "00:00:00"}

    def _send_command(self, action, soap_payload):
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{self.NS_AVT}#{action}"',
            "Connection": "close"
        }
        try:
            r = requests.post(self.control_url, data=soap_payload, headers=headers, timeout=3)
            r.raise_for_status()
        except Exception as e:
            print(f" [!] {action} command failed: {e}")
