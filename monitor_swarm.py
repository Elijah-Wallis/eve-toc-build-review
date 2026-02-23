import subprocess
import sys
import signal

# --- [CONFIGURATION: NEURAL LINK] ---
CONTAINER_NAME = "eve_autonomous_node"

def signal_handler(sig, frame):
    """Handles manual disconnect (Ctrl+C) without killing the agent."""
    print("\n\n--- [UPLINK SEVERED] ---")
    print(f"--- [TARGET '{CONTAINER_NAME}' REMAINS ACTIVE] ---")
    sys.exit(0)

def establish_uplink():
    """Streams the raw consciousness (logs) of the swarm."""
    print(f"--- [ESTABLISHING CONNECTION TO: {CONTAINER_NAME}] ---")
    
    # Verify target exists first
    check = subprocess.run(f"docker ps -q -f name={CONTAINER_NAME}", shell=True, capture_output=True)
    if not check.stdout.strip():
        print(f"--- [ERROR: TARGET '{CONTAINER_NAME}' NOT FOUND OR INACTIVE] ---")
        sys.exit(1)

    print("--- [STREAM ACTIVE. CTRL+C TO DISCONNECT] ---")
    print("------------------------------------------------")
    
    # Open the stream
    try:
        process = subprocess.Popen(
            ["docker", "logs", "-f", CONTAINER_NAME],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Project output to Architect's terminal
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            
    except Exception as e:
        print(f"--- [SIGNAL LOST: {e}] ---")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    establish_uplink()
