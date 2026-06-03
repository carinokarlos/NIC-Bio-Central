import pyodbc
import csv
import io
from collections import defaultdict
from datetime import datetime, time as dt_time
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

        # 2. Fetch ALL enrolled employees (base = backup_device_users so never-punched
        # employees still appear), LEFT JOIN to their most recent attendance log.
        # One row per employee via ROW_NUMBER on both sides.
        query = """
            WITH LatestUsers AS (
                -- One record per employee: most recent backup wins for name/access_number
                SELECT employee_code, employee_name, access_number, device_ip
                FROM (
                    SELECT
                        employee_code,
                        employee_name,
                        access_number,
                        device_ip,
                        ROW_NUMBER() OVER (
                            PARTITION BY employee_code
                            ORDER BY backup_timestamp DESC
                        ) AS rn
                    FROM dbo.backup_device_users
                ) ranked
                WHERE rn = 1
            ),
            RankedLogs AS (
                -- Most recent punch per employee, preferring registered devices
                SELECT
                    a.employee_code,
                    d.bcc        AS location_name,
                    a.punch_time,
                    ROW_NUMBER() OVER (
                        PARTITION BY a.employee_code
                        ORDER BY
                            CASE WHEN d.bcc IS NOT NULL THEN 0 ELSE 1 END,
                            a.punch_time DESC
                    ) AS rn
                FROM dbo.backup_attendance_logs a
                LEFT JOIN dbo.device_registry d
                    ON a.device_ip = d.ip_address
            ),
            LatestPunch AS (
                SELECT employee_code, location_name, punch_time
                FROM RankedLogs
                WHERE rn = 1
            )
            -- Start from ALL enrolled employees, attach punch data where available
            SELECT
                u.employee_code,
                u.employee_name,
                u.access_number,
                COALESCE(p.location_name,
                    (SELECT TOP 1 bcc FROM dbo.device_registry dr
                     WHERE dr.ip_address = u.device_ip AND bcc IS NOT NULL)
                ) AS location_name,
                p.punch_time
            FROM LatestUsers u
            LEFT JOIN LatestPunch p
                ON u.employee_code = p.employee_code
            ORDER BY p.punch_time DESC, u.employee_name ASC
        """
        cursor.execute(query)
        
        columns = [column[0] for column in cursor.description]
        logs = [dict(zip(columns, row)) for row in cursor.fetchall()]

        conn.close()

    except Exception as e:
        flash(f"Database Connection Error: {str(e)}")
        print(f"Master DB Error: {str(e)}")

    return render_template('master_database.html', logs=logs, locations=locations)


# --- API ENDPOINTS FOR DRILL-DOWN AND EXPORT ---

@master_db_bp.route('/api/employee-logs/<employee_code>', methods=['GET'])
def get_employee_logs(employee_code):
    """
    Fetches historical logs for a specific employee.
    Optional query params: date_from (YYYY-MM-DD), date_to (YYYY-MM-DD)
    Date filters are applied here so the main table always shows all employees.
    """
    if not session.get('sdr_loggedin'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    date_from = request.args.get('date_from', '').strip()
    date_to   = request.args.get('date_to',   '').strip()

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
        """
        params = [employee_code]

        if date_from:
            query += " AND a.punch_time >= ?"
            params.append(f"{date_from} 00:00:00")
        if date_to:
            query += " AND a.punch_time <= ?"
            params.append(f"{date_to} 23:59:59")

        query += " ORDER BY a.punch_time DESC"

        cursor.execute(query, params)

        columns = [column[0] for column in cursor.description]
        details = [dict(zip(columns, row)) for row in cursor.fetchall()]

        conn.close()
        return jsonify({'status': 'success', 'data': details})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@master_db_bp.route('/api/export-master-logs', methods=['POST'])
def export_master_logs():
    """
    Generates a structured CSV of attendance logs for selected employees.

    Pairing strategy (punch_type is unreliable — devices are inconsistent):
        - Time In  = max(first punch of day, 08:30)  — early arrivals clamped to business start
        - Time Out = min(last punch of day,  18:00)  — late departures clamped to business end
        - Total Hours = (Time Out - Time In) - 1h lunch break
        - Rows where clamped Time In >= Time Out are skipped (off-hours only punches)
        - If only one punch exists for the day, Time Out and Total Hours are left blank

    CSV columns:
        Employee Code | Access Code | Employee Name | Location |
        Date | Time In | Time Out | Total Hours
    """
    if not session.get('sdr_loggedin'):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    data = request.json
    emp_ids    = data.get('employees', [])
    date_from  = data.get('date_from', '')
    date_to    = data.get('date_to', '')
    locations  = data.get('locations', [])

    if not emp_ids:
        return jsonify({'status': 'error', 'message': 'No employees selected.'}), 400

    try:
        conn   = pyodbc.connect(current_app.config['BIOCENTRAL_DB'])
        cursor = conn.cursor()

        # ------------------------------------------------------------------
        # Access code subquery — picks the single most recent backup record
        # per employee so we always get exactly one access_number per person.
        # ------------------------------------------------------------------
        access_subquery = """
            SELECT employee_code, employee_name, access_number
            FROM (
                SELECT
                    employee_code,
                    employee_name,
                    access_number,
                    ROW_NUMBER() OVER (
                        PARTITION BY employee_code
                        ORDER BY backup_timestamp DESC
                    ) AS rn
                FROM dbo.backup_device_users
            ) ranked
            WHERE rn = 1
        """

        # ------------------------------------------------------------------
        # Main punch log query — ASC order so first/last detection is correct.
        # ------------------------------------------------------------------
        placeholders = ','.join('?' for _ in emp_ids)
        query = f"""
            SELECT
                a.employee_code,
                u.access_number,
                u.employee_name,
                d.bcc        AS location_name,
                a.punch_time
            FROM dbo.backup_attendance_logs a
            LEFT JOIN ({access_subquery}) u
                ON a.employee_code = u.employee_code
            LEFT JOIN dbo.device_registry d
                ON a.device_ip = d.ip_address
            WHERE a.employee_code IN ({placeholders})
        """

        params = list(emp_ids)

        if date_from:
            query += " AND a.punch_time >= ?"
            params.append(f"{date_from} 00:00:00")
        if date_to:
            query += " AND a.punch_time <= ?"
            params.append(f"{date_to} 23:59:59")
        if locations:
            loc_placeholders = ','.join('?' for _ in locations)
            query += f" AND d.bcc IN ({loc_placeholders})"
            params.extend(locations)

        query += " ORDER BY a.employee_code, a.punch_time ASC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        # ------------------------------------------------------------------
        # Group punches per employee per calendar day.
        # employee_meta carries the first-seen metadata row per employee.
        # ------------------------------------------------------------------
        employee_meta  = {}                                          # emp_code -> (access_number, name, location)
        daily_punches  = defaultdict(lambda: defaultdict(list))     # emp_code -> date_str -> [datetime, ...]

        for row in rows:
            emp_code = row.employee_code
            punch_dt = row.punch_time   # pyodbc returns datetime directly
            if not isinstance(punch_dt, datetime):
                continue
            date_str = punch_dt.strftime('%Y-%m-%d')

            if emp_code not in employee_meta:
                employee_meta[emp_code] = (
                    row.access_number if row.access_number is not None else 'NULL',
                    row.employee_name or '',
                    row.location_name or '',
                )

            daily_punches[emp_code][date_str].append(punch_dt)

        # ------------------------------------------------------------------
        # Business hours constants
        # ------------------------------------------------------------------
        BUSINESS_START  = dt_time(8, 30)    # 08:30 AM
        BUSINESS_END    = dt_time(18, 0)    # 06:00 PM
        LUNCH_BREAK_SEC = 3600              # fixed 1-hour lunch deduction

        # ------------------------------------------------------------------
        # Build CSV
        # ------------------------------------------------------------------
        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow([
            'Employee Code', 'Access Code', 'Employee Name',
            'Location', 'Date', 'Time In', 'Time Out', 'Total Hours'
        ])

        for emp_code in sorted(daily_punches.keys()):
            access_number, emp_name, location = employee_meta[emp_code]

            for date_str in sorted(daily_punches[emp_code].keys()):
                punches = sorted(daily_punches[emp_code][date_str])

                raw_in  = punches[0]
                raw_out = punches[-1] if len(punches) > 1 else None

                # Build business-hours boundary datetimes for this date
                day       = raw_in.date()
                biz_start = datetime.combine(day, BUSINESS_START)
                biz_end   = datetime.combine(day, BUSINESS_END)

                # Clamp Time In — early arrivals count from 08:30
                time_in       = max(raw_in, biz_start)   # clamped — used for hours calc only

                # Clamp Time Out — late departures cap at 18:00
                time_out = None
                if raw_out:
                    time_out = min(raw_out, biz_end)       # clamped — used for hours calc only

                # Skip rows where clamping produces an invalid window
                # (e.g. employee only punched outside business hours entirely)
                if time_out and time_in >= time_out:
                    continue

                time_in_str  = raw_in.strftime("%H:%M:%S")
                time_out_str = raw_out.strftime("%H:%M:%S") if raw_out else ''

                # Total Hours = (Time Out - Time In) - 1h lunch
                # Deduction only applies when both endpoints are present
                total_hours_str = ''
                if time_out:
                    total_secs = max(0, int((time_out - time_in).total_seconds()) - LUNCH_BREAK_SEC)
                    hours, rem = divmod(total_secs, 3600)
                    minutes, _ = divmod(rem, 60)
                    total_hours_str = f"{hours}h {minutes:02d}m"

                cw.writerow([
                    emp_code,
                    access_number,
                    emp_name,
                    location,
                    date_str,
                    time_in_str,
                    time_out_str,
                    total_hours_str,
                ])

        output = si.getvalue()
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=Biocentral_Export.csv"}
        )

    except Exception as e:
        current_app.logger.error(f"Export error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500