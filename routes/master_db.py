import pyodbc
import csv
import io
from flask import Blueprint, render_template, session, current_app, flash, redirect, url_for, jsonify, request, Response

master_db_bp = Blueprint('master_db', __name__)

@master_db_bp.route('/master-database', methods=['GET'])
def master_database():
    if not session.get('sdr_loggedin'):
        flash("Please login to access this page")
        return redirect(url_for('index', _external=True))

    logs = []
    locations = []

    try:
        conn = pyodbc.connect(current_app.config['BIOCENTRAL_DB'])
        cursor = conn.cursor()

        # 1. Fetch distinct locations for the Multi-Select Filter
        cursor.execute("SELECT DISTINCT bcc FROM dbo.device_registry WHERE bcc IS NOT NULL")
        locations = [row.bcc for row in cursor.fetchall()]

        # 2. Fetch GROUPED Master Logs (1 entry per person, showing latest log)
        query = """
            WITH RankedLogs AS (
                SELECT 
                    a.employee_code,
                    u.employee_name,
                    d.bcc AS location_name,
                    a.punch_time,
                    ROW_NUMBER() OVER(PARTITION BY a.employee_code ORDER BY a.punch_time DESC) as rn
                FROM dbo.backup_attendance_logs a
                LEFT JOIN dbo.backup_device_users u 
                    ON a.employee_code = u.employee_code AND a.device_ip = u.device_ip
                LEFT JOIN dbo.device_registry d 
                    ON a.device_ip = d.ip_address
            )
            SELECT employee_code, employee_name, location_name, punch_time 
            FROM RankedLogs 
            WHERE rn = 1
            ORDER BY punch_time DESC
        """
        cursor.execute(query)
        
        columns = [column[0] for column in cursor.description]
        logs = [dict(zip(columns, row)) for row in cursor.fetchall()]

        conn.close()

    except Exception as e:
        flash(f"Database Connection Error: {str(e)}")
        print(f"Master DB Error: {str(e)}")

    return render_template('master_database.html', logs=logs, locations=locations)


# --- NEW API ENDPOINTS FOR DRILL-DOWN AND EXPORT ---

@master_db_bp.route('/api/employee-logs/<employee_code>', methods=['GET'])
def get_employee_logs(employee_code):
    """Fetches the detailed historical logs for a specific clicked employee."""
    if not session.get('sdr_loggedin'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    try:
        conn = pyodbc.connect(current_app.config['BIOCENTRAL_DB'])
        cursor = conn.cursor()

        query = """
            SELECT 
                a.log_id,
                d.bcc AS location_name,
                a.punch_time,
                a.punch_type,
                a.verify_type
            FROM dbo.backup_attendance_logs a
            LEFT JOIN dbo.device_registry d 
                ON a.device_ip = d.ip_address
            WHERE a.employee_code = ?
            ORDER BY a.punch_time DESC
        """
        cursor.execute(query, employee_code)
        
        columns = [column[0] for column in cursor.description]
        details = [dict(zip(columns, row)) for row in cursor.fetchall()]

        conn.close()
        return jsonify({'status': 'success', 'data': details})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@master_db_bp.route('/api/export-master-logs', methods=['POST'])
def export_master_logs():
    """Generates a CSV of logs based on selected employees, dates, and locations."""
    if not session.get('sdr_loggedin'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    data = request.json
    emp_ids = data.get('employees', [])
    date_from = data.get('date_from', '')
    date_to = data.get('date_to', '')
    locations = data.get('locations', [])

    # If no employees selected, abort
    if not emp_ids:
        return jsonify({'status': 'error', 'message': 'No employees selected.'}), 400

    try:
        conn = pyodbc.connect(current_app.config['BIOCENTRAL_DB'])
        cursor = conn.cursor()

        # Build dynamic query
        query = """
            SELECT 
                a.employee_code,
                u.employee_name,
                d.bcc AS location_name,
                a.punch_time,
                a.punch_type,
                a.verify_type
            FROM dbo.backup_attendance_logs a
            LEFT JOIN dbo.backup_device_users u 
                ON a.employee_code = u.employee_code AND a.device_ip = u.device_ip
            LEFT JOIN dbo.device_registry d 
                ON a.device_ip = d.ip_address
            WHERE a.employee_code IN ({})
        """.format(','.join('?' for _ in emp_ids))

        params = list(emp_ids)

        if date_from:
            query += " AND a.punch_time >= ?"
            params.append(f"{date_from} 00:00:00")
        if date_to:
            query += " AND a.punch_time <= ?"
            params.append(f"{date_to} 23:59:59")
        if locations:
            query += " AND d.bcc IN ({})".format(','.join('?' for _ in locations))
            params.extend(locations)

        query += " ORDER BY a.employee_code, a.punch_time DESC"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Generate CSV in memory
        si = io.StringIO()
        cw = csv.writer(si)
        # Write Headers
        cw.writerow(['Employee Code', 'Employee Name', 'Location', 'Punch Time', 'Punch Type', 'Verify Type'])
        # Write Data
        for row in rows:
            cw.writerow([row.employee_code, row.employee_name, row.location_name, row.punch_time, row.punch_type, row.verify_type])

        conn.close()

        # Stream response back to browser as a download
        output = si.getvalue()
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=Biocentral_Export.csv"}
        )

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500