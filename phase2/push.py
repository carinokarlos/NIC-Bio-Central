import json
import base64
import time
from datetime import datetime
from zk import ZK
from zk.user import User
from zk.finger import Finger

# --- CONFIGURATION ---
TARGET_IP = '192.168.10.100'
TARGET_PORT = 4370
INPUT_FILE = 'master_user_data.json'
DELAY = 0.1

# --- THE SPECIFIC EMPLOYEES TO PUSH ---
TARGET_EMPLOYEES = ['11111512', '40626479', '11110048', '12925456']

def connect_device():
    zk = ZK(TARGET_IP, port=TARGET_PORT, timeout=15, force_udp=False)
    conn = zk.connect()
    conn.disable_device()
    conn.refresh_data = lambda: None
    return conn

def push_master_data():
    print(f"[{datetime.now()}] Loading data from {INPUT_FILE}...")
    with open(INPUT_FILE, 'r') as f:
        master_list = json.load(f)

    # Filter the list to ONLY include the target employees
    filtered_list = [
        user for user in master_list 
        if str(user.get('user_id', user.get('emp_id', ''))) in TARGET_EMPLOYEES
    ]

    total = len(filtered_list)
    if total == 0:
        print("[!] None of the targeted employees were found in the JSON file. Aborting.")
        return

    failed = []
    conn = None

    try:
        conn = connect_device()
        print(f"Connected. Pushing {total} targeted users...")

        for index, user in enumerate(filtered_list, start=1):
            uid = index
            original_id = str(user.get('user_id', user.get('emp_id', '')))

            try:
                # Safely get PIN/Password to avoid KeyError
                raw_pin = user.get('pin', user.get('password', ''))
                
                # Safely get Privilege
                raw_priv = user.get('privilege', 0)
                privilege = int(raw_priv) if raw_priv is not None else 0

                print(f"  -> Pushing {original_id} | {user.get('name', 'Unknown')} | PIN: {raw_pin if raw_pin else 'None'}")

                user_obj = User(
                    uid=uid,
                    name=user.get('name', ''),
                    privilege=privilege,
                    password=str(raw_pin),
                    user_id=original_id
                )

                # Safely handle the templates array
                templates = user.get('templates', user.get('fingerprints', []))
                fingers = [
                    Finger(
                        uid=uid,
                        fid=int(t.get('fid', t.get('finger_index', 0))),
                        valid=int(t.get('valid', t.get('is_valid', 1))),
                        template=base64.b64decode(t.get('template', t.get('template_data', '')))
                    )
                    for t in templates
                    if int(t.get('fid', t.get('finger_index', 0))) <= 9  # Only allow fid 0-9
                ]

                # Push the profile (including password) and fingerprints
                conn.save_user_template(user_obj, fingers)
                time.sleep(DELAY)

            except Exception as e:
                print(f"  [!] FAILED: {original_id} | {user.get('name', '')} — {e}")
                failed.append({
                    "user_id": original_id,
                    "name": user.get('name', ''),
                    "error": str(e)
                })
                # Reconnection fallback
                try:
                    conn.disconnect()
                except Exception:
                    pass
                time.sleep(2)
                try:
                    conn = connect_device()
                except Exception as re:
                    print(f"  [!] Reconnect failed: {re}")
                continue

        print("-" * 50)
        print(f"SUCCESS: {total - len(failed)}/{total} targeted users pushed to {TARGET_IP}")
        if failed:
            print(f"FAILED:  {len(failed)} users — saved to failed_users.json")
            with open('failed_users.json', 'w') as f:
                json.dump(failed, f, indent=4)
        print("-" * 50)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Critical Error: {e}")
    finally:
        if conn:
            try:
                conn.enable_device()
                conn.disconnect()
                print("Device communication closed.")
            except Exception:
                print("Device already disconnected.")

if __name__ == "__main__":
    push_master_data()