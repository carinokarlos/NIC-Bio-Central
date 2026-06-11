import socket
from zk import ZK
import time

#NIC BIO CENTRAL SUGGESTED APP NAME 

def test_connectivity(ip, port=4370, timeout=10):
    print(f"[{ip}] Validating TCP route...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        print(f"[{ip}] TCP Port {port} is OPEN. Network routing is valid.")
        return True
    except Exception as e:
        print(f"[{ip}] Connectivity failed: {e}")
        return False
    finally:
        s.close()

def test_enrollment(ip, test_employee_id="420002"):
    print(f"[{ip}] Initiating pyzk handshake...")
    zk = ZK(ip, port=4370, timeout=15, force_udp=False) 
    conn = None
    
    try:
        start_time = time.time()
        conn = zk.connect()
        handshake_time = time.time() - start_time
        print(f"[{ip}] Handshake successful in {handshake_time:.2f}s.")

        # Disable the device to put it into 'command mode'
        print(f"[{ip}] Locking device for enrollment...")
        conn.disable_device()

        print(f"[{ip}] Triggering enrollment UI for ID {test_employee_id}...")
        
        # 2. Use enroll_user. 
        # uid: Internal numeric ID. user_id: String ID. temp_id: Finger index (0-9).
        # Most ZK firmwares expect the ID as an integer for the protocol.
        result = conn.enroll_user(
            uid=int(test_employee_id), 
            temp_id=0, 
            user_id=str(test_employee_id)
        )
        
        if result:
            print(f"[{ip}] SUCCESS: Device accepted enrollment mode. Check physical screen.")
        else:
            print(f"[{ip}] FAIL: Device rejected command. Check if user already exists.")
            
    except AttributeError:
        print(f"[{ip}] Error: Your pyzk version doesn't support 'enroll_user'. Check documentation.")
    except Exception as e:
        print(f"[{ip}] Hardware communication failed: {e}")
    finally:
        if conn:
            # 3. CRITICAL: Re-enable the device so users can clock in again
            print(f"[{ip}] Re-enabling device and disconnecting...")
            conn.enable_device()
            conn.disconnect()

# Run the test
target_ip = "192.168.10.100" # Insertremote store IP 
if test_connectivity(target_ip):
    test_enrollment(target_ip)
# test_connectivity(target_ip)