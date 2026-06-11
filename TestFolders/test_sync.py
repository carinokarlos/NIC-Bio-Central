from zk import ZK
from datetime import datetime

# Your Device IP
DEVICE_IP = '192.168.10.100' 
DEVICE_PORT = 4370

def sync_device_time():
    zk = ZK(DEVICE_IP, port=DEVICE_PORT, timeout=5)
    conn = None
    
    try:
        conn = zk.connect()
        print(f"Connected to {DEVICE_IP}")

        # 1. Get current device time before sync
        old_time = conn.get_time()
        print(f"Current Device Time: {old_time}")

        # 2. Get current Server/PC time
        server_time = datetime.now()
        print(f"Current Server Time: {server_time.strftime('%Y-%m-%d %H:%M:%S')}")

        # 3. Sync the time
        # The set_time function expects a Python datetime object
        conn.set_time(server_time)
        print("✅ Device time has been synchronized with the server.")

    except Exception as e:
        print(f"❌ Failed to sync time: {e}")
    finally:
        if conn:
            conn.disconnect()
            print("Disconnected.")

if __name__ == "__main__":
    sync_device_time()