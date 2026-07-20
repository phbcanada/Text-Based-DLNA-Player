import threading
import time
import os

import threading
import requests
import socket
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, HTTPServer

# This handles the inbound network traffic from the device
class GENAEventHTTPHandler(BaseHTTPRequestHandler):
    def do_NOTIFY(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            payload = self.rfile.read(content_length).decode('utf-8')
            if "LastChange" in payload:
                self.process_transport_event(payload)
        
        # Always return HTTP 200 OK to acknowledge the event receipt
        self.send_response(200)
        self.end_headers()

    def process_transport_event(self, xml_payload):
        try:
            if isinstance(xml_payload, bytes):
                xml_payload = xml_payload.decode('utf-8', errors='ignore')
                
            # Keep a broad pre-parse fix for broken XML ampersands
            sanitized_xml = xml_payload.replace("& ", "&amp; ")
            
            root = ET.fromstring(sanitized_xml)
            for item in root.iter():
                if 'LastChange' in item.tag:
                    inner_xml = item.text
                    if not inner_xml:
                        continue
                    
                    sanitized_inner = inner_xml.replace("& ", "&amp; ")
                    try:
                        inner_root = ET.fromstring(sanitized_inner)
                    except ET.ParseError:
                        # Backup regex fall-back if the hardware outputs garbage XML
                        import re
                        state_match = re.search(r'TransportState\s+val="([^"]+)"', inner_xml)
                        if state_match:
                            class DummyNode:
                                tag = 'TransportState'
                                attrib = {'val': state_match.group(1)}
                            inner_root = [DummyNode()]
                        else:
                            continue
                        
                    for state_node in inner_root.iter():
                        if 'TransportState' in state_node.tag:
                            state = state_node.attrib.get('val', '')
                            play_queue = self.server.play_queue
                            
                            # Safely read and mutate under the shared state lock
                            with play_queue.state_lock:
                                current_was_playing = play_queue.was_playing
                                
                                print(f"[GENA STATE DEBUG] Device broadcasted: {state} (Current queue was_playing={current_was_playing})")
                                
                                if state in ("PLAYING", "TRANSITIONING"):
                                    play_queue.was_playing = True
                                    
                                elif state in ("STOPPED", "NO_MEDIA_PRESENT", "PAUSED_PLAYBACK"):
                                    if current_was_playing and state != "PAUSED_PLAYBACK":
                                        play_queue.was_playing = False
                                        print("[*] Advancing queue...")
                                        play_queue.next()
                                        # threading.Thread(target=play_queue.next, daemon=True).start()
                                    
        except Exception as e:
            print(f"[-] GENA Parsing Error: {e}")

    def log_message(self, format, *args):
        # print(f"[HTTP Server Log] {format % args}")
        pass


class PlayQueue:
    def __init__(self, renderer, *args, **kwargs):
        self.renderer = renderer
        self.queue = []            # List of dicts: [{'title': x, 'uri': y, 'mime': z}]
        self.current_idx = -1
        
        self.lock = threading.Lock()
        self.running = True
        self.state_lock = threading.Lock()
        self.was_playing = False
        
        # =====================================================================
        # EXPERIMENTAL TOGGLE: Set to True to override monitor loop with GENA
        # =====================================================================
        self.use_gena = True 
        

    def _get_local_ip_prev(self):
        """Helper to find the correct local interface talking to the device."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((self.renderer.host, 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except Exception:
            return "0.0.0.0"

    def _get_local_ip(self):
        """Forces finding the real local IP interface facing the renderer."""
        try:
            from urllib.parse import urlparse
            import socket
            
            # Read the host directly out of the live event URL string
            event_url = getattr(self.renderer, 'avtransport_event_url', "")
            if event_url:
                target_host = urlparse(event_url).hostname
            else:
                target_host = self.renderer.host # Fallback if empty
                
            if not target_host or target_host.lower() == "unknown":
                return "0.0.0.0"

            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target_host, 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except Exception as e:
            print(f"[-] Auto-IP detection failed: {e}")
            return "0.0.0.0"

    def _start_gena_listener(self):
        """Spins up the HTTP backend and sends the SUBSCRIBE packet safely."""
        local_ip = self._get_local_ip()
        local_port = 8089  # Choose any open port on your system

        print(f"[*] Local GENA listener binding to: http://{local_ip}:{local_port}")
        if local_ip in ["0.0.0.0", "127.0.0.1"]:
            print("[-] WARNING: Local IP resolved to loopback. gmediarender won't route back to this!")

        def run_server():
            try:
                # Initialize standard python HTTP server attached to our handler
                server = HTTPServer((local_ip, local_port), GENAEventHTTPHandler)
                server.play_queue = self  # Give the handler access to this queue instance
                server.serve_forever()
            except Exception as e:
                print(f"[-] GENA Background Server failed: {e}. Falling back to monitor thread.")
                self._start_local_monitor_thread()

        # 1. Start the local server background thread immediately
        threading.Thread(target=run_server, daemon=True).start()

        # 2. Define the subscription sequence
        def send_subscribe():
            event_url = getattr(self.renderer, 'avtransport_event_url', None)
            if not event_url:
                self._start_local_monitor_thread()
                return

            headers = {
                "HOST": event_url.split("://")[1].split("/")[0],
                "TIMEOUT": "Second-300"
            }

            # =====================================================================
            # DYNAMIC RENEWAL LOGIC: Switch between initial sub and a lease renewal
            # =====================================================================
            current_sid = getattr(self, 'gena_sid', None)
            
            if current_sid:
                # We already have an active session! This is a RENEWAL request.
                headers["SID"] = current_sid
                print(f"[*] Renewing GENA Subscription lease for SID: {current_sid}...")
            else:
                # Fresh boot. This is an INITIAL subscription request.
                headers["CALLBACK"] = f"<http://{local_ip}:{local_port}/>"
                headers["NT"] = "upnp:event"
                print(f"[*] Sending INITIAL SUBSCRIBE to endpoint: {event_url}...")

            try:
                # Both initial registration and renewals use the HTTP 'SUBSCRIBE' verb
                res = requests.request("SUBSCRIBE", event_url, headers=headers, timeout=5)
                
                if res.status_code == 200:
                    # Capture or reaffirm our token ID
                    if not current_sid:
                        self.gena_sid = res.headers.get('SID')
                        print(f"[+] GENA subscription active! SID: {self.gena_sid}")
                    else:
                        print("[+] GENA subscription lease successfully renewed!")

                    # --- THE AUTO-RENEWAL TIMER ---
                    # Even if the device claims 300 seconds, we refresh every 150 seconds for safety
                    if getattr(self, 'use_gena', True):
                        self.renewal_timer = threading.Timer(150.0, send_subscribe)
                        self.renewal_timer.daemon = True
                        self.renewal_timer.start()
                else:
                    print(f"[-] GENA request rejected ({res.status_code}). Dropping back to polling.")
                    self._start_local_monitor_thread()
                    
            except Exception as e:
                print(f"[-] GENA handshake error: {e}. Dropping back to polling.")
                self._start_local_monitor_thread()

        # =====================================================================
        # THE MISSING LINK: Actually execute the send_subscribe sequence!
        # A 1.0 second delay guarantees the HTTP server thread above is fully
        # listening and bound to port 8089 before gmediarender starts hitting it.
        # =====================================================================
        threading.Timer(1.0, send_subscribe).start()

    def _start_local_monitor_thread(self):
        """Your existing, working local polling loop code goes here."""
        print("[*] Launching standard background polling monitor thread...")
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        pass

    def save_playlist(self, playlist_name):
        """Generates a local server-compatible M3U file from the current queue tracks."""
        if not playlist_name:
            playlist_name = "my_playlist"
            
        save_dir = os.path.expanduser("~/.playlists")
        os.makedirs(save_dir, exist_ok=True)
        filepath = os.path.join(save_dir, f"{playlist_name}.m3u")
        
        try:
            with self.lock:
                tracks_snapshot = list(self.queue)
                
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for track in tracks_snapshot:
                    rel_path = track.get('relative_path')
                    uri = track.get('uri')
                    if rel_path and uri:
                        f.write(f"#EXTINF:-1,{track['title']}\n")
                        f.write(f"#URI:{uri}\n")  # Keeps live network link intact for loading
                        f.write(f"{rel_path}\n")
                        
            print(f"\n[+] Playlist successfully saved locally to: {filepath}")
            print("[i] Copy this file straight to your MiniDLNA directory to view across your network.")
            return True
        except Exception as e:
            print(f"[!] Failed to save playlist: {e}")
            return False

    def load_playlist(self, playlist_name):
        """Loads and appends tracks from a local M3U file back into the active play queue."""
        save_dir = os.path.expanduser("~/.playlists")
        filepath = os.path.join(save_dir, f"{playlist_name}.m3u")
        
        if not os.path.exists(filepath):
            print(f"[!] Playlist file not found: {filepath}")
            return False
            
        try:
            new_tracks = []
            current_title = "Unknown Track"
            current_uri = None
            
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#EXTM3U"):
                        continue
                        
                    if line.startswith("#EXTINF:"):
                        parts = line.split(",", 1)
                        if len(parts) > 1:
                            current_title = parts[1]
                    elif line.startswith("#URI:"):
                        current_uri = line[5:].strip()
                    elif not line.startswith("#"):
                        # This line is the physical relative path
                        uri = current_uri if current_uri else (line if line.startswith("http") else "")
                        if uri:
                            new_tracks.append({
                                'title': current_title,
                                'uri': uri,
                                'relative_path': line
                            })
                        # Reset temporary accumulators for next track
                        current_title = "Unknown Track"
                        current_uri = None
                        
            if new_tracks:
                with self.lock:
                    initial_empty = len(self.queue) == 0
                    self.queue.extend(new_tracks)
                    if initial_empty and self.current_idx == -1:
                        self.current_idx = 0
                print(f"\n[+] Successfully loaded {len(new_tracks)} tracks from '{playlist_name}' into the queue.")
                return True
            
            print("[-] No valid streaming tracks could be loaded from this file.")
            return False
        except Exception as e:
            print(f"[!] Failed to load playlist: {e}")
            return False

    def add_to_queue(self, track_item):
        with self.lock:
            self.queue.append(track_item)
            print(f"\n[+] Queued: {track_item['title']}")
            if self.current_idx == -1:
                self.current_idx = 0

    def play_now(self, track_item):
        with self.lock:
            insert_pos = self.current_idx + 1 if self.current_idx != -1 else 0
            self.queue.insert(insert_pos, track_item)
            self.current_idx = insert_pos
            self._play_current()

    def play(self):
        with self.lock:
            if not self.queue:
                print("Queue is empty.")
                return
            if self.current_idx == -1:
                self.current_idx = 0
            self._play_current()

    def pause(self):
        self.renderer.pause()

    def toggle_play(self):
        """Toggles between play and pause depending on the renderer's active state."""
        try:
            # Query the renderer's transport state directly
            state = self.renderer.get_transport_state()
        except Exception:
            state = "STOPPED"

        if state in ("PLAYING", "TRANSITIONING"):
            print("\n[*] Toggling: Pausing playback.")
            self.pause()
        else:
            print("\n[*] Toggling: Resuming/Starting playback.")
            self.play()

    def stop(self):
        self.was_playing = False
        self.renderer.stop()

    def next(self):
        with self.lock:
            if self.current_idx + 1 < len(self.queue):
                self.current_idx += 1
                self._play_current()
            else:
                print("\n[!] End of Play Queue reached.")
                self.stop()

    def prev(self):
        with self.lock:
            if self.current_idx > 0:
                self.current_idx -= 1
                self._play_current()
            else:
                print("\n[!] Already at the first track.")

    def clear(self):
        with self.lock:
            self.stop()
            self.queue.clear()
            self.current_idx = -1
            print("Queue cleared.")

    def get_current_track(self):
        with self.lock:
            if 0 <= self.current_idx < len(self.queue):
                return self.queue[self.current_idx]
            return None

    def display_queue(self):
        with self.lock:
            print("\n" + "="*50)
            print(" 🎶 CURRENT PLAY QUEUE:")
            print("="*50)
            if not self.queue:
                print("   (Queue is empty)")
            else:
                for idx, track in enumerate(self.queue):
                    prefix = "➔ ▶ " if idx == self.current_idx else "    "
                    print(f"{prefix}{idx + 1}. {track['title']}")
                    # print(f"{prefix}{idx + 1}. {track}")
            print("="*50)

    def _play_current(self):
        if 0 <= self.current_idx < len(self.queue):
            track = self.queue[self.current_idx]
            self.was_playing = False

            if self.renderer.play_uri(track['uri'], track['title']):
                self.was_playing = True

    def _monitor_loop(self):
        """Background thread loop verifying track status every second."""
        while self.running:
            try:
                if self.renderer.control_url:
                    state = self.renderer.get_transport_state()
                    
                    if state in ("PLAYING", "TRANSITIONING"):
                        self.was_playing = True
                    elif state in ("STOPPED", "NO_MEDIA_PRESENT", "PAUSED_PLAYBACK"):
                        if self.was_playing and state != "PAUSED_PLAYBACK":
                            self.was_playing = False
                            self.next()
            except Exception:
                pass
            time.sleep(1.5)

    def start_monitoring(self):
        # Initialize the chosen tracking mechanism
        if self.use_gena:
            self._start_gena_listener()
        else:
            self._start_local_monitor_thread()

    def shutdown(self):
        """Cleans up background assets on application close."""
        print("[*] Tearing down PlayQueue resources...")
        # Stop our auto-renewal loop from firing again
        if hasattr(self, 'renewal_timer'):
            self.renewal_timer.cancel()
            
        self.running = False
