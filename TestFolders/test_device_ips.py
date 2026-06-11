import mysql.connector
from tabulate import tabulate

def test_fetch_all_device_ips():
    print("=" * 65)
    print(" TESTING BIOCENTRAL HARDWARE REGISTRY: DEVICE IP SNAPSHOT ")
    print("=" * 65)
    
    # --- phpMyAdmin / MySQL Local Database Configuration ---
    db_config = {
        'host': '127.0.0.1',        # Or your MySQL Server IP
        'user': 'root',             # Your phpMyAdmin database username
        'password': '',             # Your phpMyAdmin database password
        'database': 'biocentral'
    }
    
    connection = None
    try:
        connection = mysql.connector.connect(**db_config)
        cursor = connection.cursor(dictionary=True)
        
        # Querying from your core registry table
        query = """
            SELECT 
                backup_id,
                device_ip,
                COUNT(employee_code) as enrolled_users,
                MAX(backup_timestamp) as last_sync
            FROM backup_device_users
            GROUP BY device_ip
        """
        
        cursor.execute(query)
        devices = cursor.fetchall()
        
        if not devices:
            print("\n[!] Connection successful, but no backed up device IPs found.")
            return
            
        # Formulate data matrix into a structured terminal grid
        table_data = []
        for index, dev in enumerate(devices, 1):
            table_data.append([
                index,
                dev['device_ip'],
                f"{dev['enrolled_users']} Profiles",
                dev['last_sync']
            ])
            
        headers = ["Node #", "Hardware Device IP", "Backup Identity Volume", "Last Context Capture"]
        print(tabulate(table_data, headers=headers, tablefmt="fancy_grid"))
        print(f"\n[SUCCESS] Matrix validation complete. Total unique terminal IPs discovered: {len(devices)}")
        
    except mysql.connector.Error as err:
        print(f"\n[DATABASE ERROR] Failed to parse device registry table: {err}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()

if __name__ == "__main__":
    # Note: Requires 'pip install mysql-connector-python tabulate'
    test_fetch_all_device_ips()