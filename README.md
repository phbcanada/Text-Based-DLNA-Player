# DLNA / UPnP Controller & Experimental Browser

A lightweight, terminal-based Python client built to explore fundamental **DLNA / UPnP protocols** and test real-world behavior and edge cases across various UPnP hardware and software renderers (e.g., GGMM speakers, `gmediarender`, MiniDLNA, Jellyfin).

---

## 🎯 Purpose & Scope

This project was developed primarily as an experimental testbed to:
1. **Understand Core UPnP Protocols**: Gain hands-on experience with SSDP device discovery, SOAP-based Control actions (AVTransport / RenderingControl), and GENA eventing.
2. **Troubleshoot Renderer Issues**: Investigate non-standard behaviors, edge cases, and protocol quirks in commercial hardware (e.g., GGMM multi-room speakers) versus open-source renderers (`gmediarender`).
3. **Test Event-Driven Architecture vs. Polling**: Experiment with switching from traditional state polling to asynchronous UPnP Event Subscriptions (GENA).

---

## 🛠️ How It Works

The application operates as an interactive command-line DLNA Control Point:

### 1. Discovery & Browsing
* **SSDP Discovery**: Broadcasts M-SEARCH requests over UDP to auto-discover active DLNA media servers (e.g., MiniDLNA, Jellyfin) and renderers on the local network.
* **Content Browsing**: Sends SOAP XML requests to media servers to traverse directory hierarchies (including raw "Folder" views) and extract track URIs and metadata.

### 2. Playback Control & Queue Management
* **Control Pipeline**: Sends SOAP actions (`SetAVTransportURI`, `Play`, `Pause`, `Stop`, `Seek`) to selected renderers.
* **Play Queue**: Maintains a client-side track queue, managing track advancement and playback status.

### 3. State Tracking: GENA Subscriptions vs. Polling
To keep track of playback progress (e.g., detecting when a song ends to advance the queue):
* **GENA Protocol (Experimental)**: Spins up a local HTTP server and sends a `SUBSCRIBE` HTTP request to the renderer's `eventSubURL`. The renderer pushes asynchronous `NOTIFY` packets containing XML state updates back to the client.
* **Polling Loop (Fallback)**: If a device rejects GENA subscriptions (e.g., returning HTTP 500 or failing network callbacks), the queue gracefully falls back to periodic SOAP status checks.

---

## 🧱 Key Components

* `main.py`: Main startup script and entry point for the application, initializing the interactive CLI shell and event loop.
* `controller.py`: Core logic managing user interface actions, playback commands, and key bindings.
* `dlnabrowser.py`: Handles SSDP discovery, XML parsing, and media server navigation.
* `playqueue.py`: Manages the playback queue, background status monitoring, and track transitions.
* `gena_listener`: Local HTTP server catching inbound UPnP state notifications (`NOTIFY`).

---

## 🚀 Getting Started

### Prerequisites
* Python 3.8+
* Dependencies: `requests`, `urllib3` (standard network dependencies)

### Running the Controller
Execute the startup script from your terminal:

$ python main.py

1. Select your target **Media Server** and **Media Renderer** from the auto-discovered list.
2. Browse your server's folder layout and queue tracks.
3. Use single-key terminal commands to control playback, adjust volume, or inspect live state metadata.

---

## 🔬 Key Learnings & Test Findings

* **GENA Support Varies**: Open-source renderers like `gmediarender` (`gmrender-resurrect`) fully support GENA eventing, whereas certain embedded hardware appliances reject standard subscription headers or omit event callbacks entirely.
* **Network Callbacks & Interfaces**: GENA event delivery requires proper local interface binding (avoiding `0.0.0.0` or loopback addresses) and open inbound ports through local firewalls.
* **State Machine Protection**: Race conditions (such as brief `STOPPED` states during track loading) require safety flags in the queue state machine to prevent accidental double-skipping.
