import socket
from zk import ZK, const
import time

def test_connectivity(ip, port=4370, timeout=2):
    print(f"[{ip}] Validating TCP route...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        print(f"[{ip}] TCP Port {port} is OPEN.")
        return True
    except Exception as e:
        print(f"[{ip}] Connectivity failed: {e}")
        return False
    finally:
        s.close()

def push_and_enroll_employee(ip, new_user_id, new_name):
    print(f"[{ip}] Initiating pyzk handshake...")
    zk = ZK(ip, port=4370, timeout=10)
    conn = None
    
    try:
        conn = zk.connect()
        print(f"[{ip}] Handshake successful.")
        
        # --- STEP 1: Calculate Internal UID ---
        print(f"[{ip}] Fetching existing device users to calculate internal UID...")
        users = conn.get_users()
        
        # Check if the HR ID already exists on the device to prevent duplicates
        if any(u.user_id == new_user_id for u in users):
            print(f"[{ip}] ABORT: Employee ID {new_user_id} already exists on this device.")
            return

        # Find the highest internal UID and add 1
        if users:
            next_uid = max(u.uid for u in users) + 1
        else:
            next_uid = 1
            
        print(f"[{ip}] Internal index calculated as: {next_uid}")

        # --- STEP 2: Push the Profile ---
        print(f"[{ip}] Locking device to push new profile: {new_name} ({new_user_id})...")
        conn.disable_device() # Lock the UI so nobody clocks in while we write data
        
        # const.USER_DEFAULT = 0 (Standard User). Use 14 for Super Admin.
        conn.set_user(
            uid=next_uid, 
            name=new_name, 
            privilege=const.USER_DEFAULT, 
            password='', 
            group_id='', 
            user_id=new_user_id
        )
        
        conn.enable_device() # Unlock the UI
        print(f"[{ip}] Profile successfully written to hardware.")

        # --- STEP 3: Trigger Physical Enrollment ---
        print(f"[{ip}] Sending start_enroll command for ID {new_user_id}...")
        # The device should beep immediately here
        result = conn.start_enroll(new_user_id, temp_id=0, flag=1)
        
        if result:
            print(f"[{ip}] SUCCESS: Device accepted enrollment mode. Please place finger.")
        else:
            print(f"[{ip}] FAIL: Device rejected enrollment command (might be busy).")
            
    except Exception as e:
        print(f"[{ip}] Hardware communication failed: {e}")
    finally:
        # ALWAYS unlock the device in the finally block if something crashed
        if conn:
            try:
                conn.enable_device()
            except:
                pass
            conn.disconnect()
            print(f"[{ip}] Connection cleanly severed.")

# Run the test
target_ip = "192.168.10.100"     # CHANGE THIS to your store device IP
test_hr_id = "420002"      # Must be strictly numeric
test_name = "Carlos Cabral"  # Max 24 characters usually

if test_connectivity(target_ip):
    push_and_enroll_employee(target_ip, test_hr_id, test_name)