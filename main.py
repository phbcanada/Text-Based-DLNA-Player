from dlnarenderer import DLNARenderer
from dlnabrowser import DLNABrowser
from playqueue import PlayQueue
from controller import Controller

def main():
    renderer = DLNARenderer()
    browser = DLNABrowser()
    
    try:
        # Step 1: Discover / Select Renderer Output
        renderer.select_renderer()
        print("")
        
        # Step 2: Initialize Play Queue Threading Engine
        queue = PlayQueue(renderer)
        
        # Step 3: Discover / Select Media Server
        if browser.select_server():
            print(f"[+] Active Control URL: {browser.control_url}\n")
            
            # Step 4: Run controller loop
            controller = Controller(queue, browser)
            controller.run()
        else:
            queue.shutdown()
            print("Exiting.")
            
    except Exception as e:
        print(f"\n[!] Global error: {e}")

if __name__ == "__main__":
    main()
