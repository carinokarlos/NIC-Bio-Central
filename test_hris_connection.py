import pyodbc

# Configuration details
server = 'MGSVR15'
database = 'HRISNICV2'
username = 'BioCentral'
password = 'B1oC3ntr@l2026'
table = 'vBiometricsManagement'

# Connection String using SQL Server Authentication
conn_str = (
    f'DRIVER={{SQL Server}};'
    f'SERVER={server};'
    f'DATABASE={database};'
    f'UID={username};'
    f'PWD={password};'
)

def test_db_connection():
    conn = None
    try:
        print(f"Attempting to connect to {server} as {username}...")
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()

        # Query the top 100 records
        query = f"SELECT TOP 100 [Code], [Name], [AccessNo] FROM {table}"
        cursor.execute(query)
        
        rows = cursor.fetchall()
        
        # Display Results
        print(f"\nConnection Successful!")
        print(f"Retrieved {len(rows)} records from {table}:")
        print("-" * 60)
        print(f"{'Code':<15} | {'Name':<30} | {'AccessNo':<10}")
        print("-" * 60)
        
        for row in rows:
            # Using getattr to handle potential None values gracefully
            code = str(row.Code) if row.Code else ""
            name = str(row.Name) if row.Name else ""
            access_no = str(row.AccessNo) if row.AccessNo else ""
            print(f"{code:<15} | {name:<30} | {access_no:<10}")

    except pyodbc.Error as ex:
        sqlstate = ex.args[1]
        print(f"Database Error: {sqlstate}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()
            print("\nDatabase connection closed.")

if __name__ == "__main__":
    test_db_connection()