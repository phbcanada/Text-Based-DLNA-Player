import threading
import time

class PlayQueue:
    """Manages list of tracks, current playback state, and automatic track advancing via a monitor thread."""
    def __init__(self, renderer):
        self.renderer = renderer
        self.queue = []            # List of dicts: [{'title': x, 'uri': y, 'mime': z}]
        self.current_idx = -1
        
        self.lock = threading.Lock()
        self.running = True
        self.was_playing = False   # State-tracking flag for track end transitions
        
        # Monitor Thread Start
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

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
            print("="*50)

    def _play_current(self):
        if 0 <= self.current_idx < len(self.queue):
            track = self.queue[self.current_idx]
            self.was_playing = False  # Reset state before starting new track
            self.renderer.play_uri(track['uri'], track['title'])

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

    def shutdown(self):
        self.running = False
