import json
import time
import base64  # Added for binary encoding
from datetime import datetime
from zk import ZK

# --- CONFIGURATION ---
DEVICE_IP = '192.168.100.162'  # Update this to your device IP
DEVICE_PORT = 4370
OUTPUT_FILE = 'master_user_data.json'

def fetch_master_data():
    zk = ZK(DEVICE_IP, port=DEVICE_PORT, timeout=15, force_udp=False)
    conn = None
    
    try:
        print(f"[{datetime.now()}] Connecting to device...")
        conn = zk.connect()
        conn.disable_device()
        print("Connected. Extracting User Roster and Biometric Templates...")

        users = conn.get_users()
        templates = conn.get_templates()

        master_list = []
        for user in users:
            # Handle names that might be returned as bytes
            user_name = user.name.decode('utf-8') if isinstance(user.name, bytes) else user.name
            
            user_templates = [t for t in templates if t.uid == user.uid]
            
            user_data = {
                "user_id": user.user_id,
                "name": user_name,
                "privilege": user.privilege,
                "pin": user.password,        # <-- added
                "template_count": len(user_templates),
                "templates": [
                    {
                        "fid": t.fid,
                        "valid": t.valid,
                        "template": base64.b64encode(t.template).decode('utf-8')
                    } for t in user_templates
                ]
            }
            master_list.append(user_data)

        with open(OUTPUT_FILE, 'w') as f:
            json.dump(master_list, f, indent=4)

        print("-" * 30)
        print(f"SUCCESS: Master Data Saved to {OUTPUT_FILE}")
        print(f"Total Users: {len(users)} | Total Templates: {len(templates)}")
        print("-" * 30)

    except Exception as e:
        # Use repr(e) to see the full error details if it fails again
        print(f"Critical Error: {e}")
    finally:
        if conn:
            conn.enable_device()
            conn.disconnect()
            print("Device communication closed.")

if __name__ == "__main__":
    fetch_master_data()