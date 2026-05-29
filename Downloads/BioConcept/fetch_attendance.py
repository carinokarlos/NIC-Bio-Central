import csv
import time
from datetime import datetime
from zk import ZK

# --- CONFIGURATION ---
DEVICE_IP = '192.168.100.161'  # Replace with your actual device IP
DEVICE_PORT = 4370
OUTPUT_FILE = 'attendance_logs.csv'

def fetch_data():
    zk = ZK(DEVICE_IP, port=DEVICE_PORT, timeout=15)
    conn = None
    
    try:
        print(f"[{datetime.now()}] Connecting to device at {DEVICE_IP}...")
        conn = zk.connect()
        
        # 1. Disable device to prevent interference during data transfer
        conn.disable_device()
        print("Connection established. Fetching logs...")

        # 2. Get all attendance records
        attendance = conn.get_attendance()
        
        if not attendance:
            print("No records found on the device.")
            return

        # 3. Save to PC as CSV
        print(f"Found {len(attendance)} records. Saving to {OUTPUT_FILE}...")
        
        with open(OUTPUT_FILE, mode='w', newline='') as file:
            writer = csv.writer(file)
            # Writing headers
            writer.writerow(["User ID", "Timestamp", "Status", "Punch Type"])
            
            for log in attendance:
                # Status/Punch mapping can vary by firmware, 
                # but generally 0 = Check In, 1 = Check Out
                writer.writerow([
                    log.user_id, 
                    log.timestamp, 
                    log.status, 
                    log.punch
                ])

        print(f"Successfully exported {len(attendance)} logs.")

    except Exception as e:
        print(f"Process failed: {e}")
        
    finally:
        if conn:
            # 4. ALWAYS re-enable device or it will remain locked!
            conn.enable_device()
            conn.disconnect()
            print("Device re-enabled and disconnected.")

if __name__ == "__main__":
    fetch_data()