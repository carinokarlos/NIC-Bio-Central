from flask import session, jsonify, request, render_template, redirect, url_for, flash
from portal import app, loggedin_required
from datetime import datetime, timedelta, date
from urllib.parse import urlparse, urljoin
import ldap
import pyodbc

# ==========================================
# SECURITY HELPER: Prevent Open Redirects
# ==========================================
def is_safe_url(target):
    """Ensures the 'next' redirect URL is on the same domain."""
    ref_url  = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

# ==========================================
# STATUS CHECK
# ==========================================
@app.route('/statuschk', methods=['GET', 'POST'])
def statuschk():
    return jsonify({"status": "Site is OK"})

# ==========================================
# MAIN LOGIN LOGIC
# ==========================================
@app.route('/', methods=['GET', 'POST'])
def index():
    # UX: If already logged in, skip the login page entirely
    if session.get('sdr_loggedin'):
        return redirect(url_for('home'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        # Guard against empty submissions
        if not username or not password:
            flash("Username and password are required.")
            return redirect(url_for('index'))

        # FIX 3: Read 'next' from the form (hidden field) OR query string.
        # On a POST, request.args is empty — the 'next' param must come
        # from a hidden input rendered in the login template, e.g.:
        #   <input type="hidden" name="next" value="{{ request.args.get('next', '') }}">
        next_page = request.form.get('next') or request.args.get('next')

        MIS_SysDev_connect = None
        MIS_SysDev_cursor  = None
        ldap_conn          = None

        try:
            # 1. Establish Database Connection
            rule = request.url_rule.rule if request.url_rule else '/'
            MIS_SysDev_connect = pyodbc.connect(f"{app.config['MIS_SysDev']}app={rule}")
            MIS_SysDev_cursor  = MIS_SysDev_connect.cursor()

            # 2. Establish LDAP Connection
            ldap_conn = ldap.initialize(app.config['LDAP_PROVIDER_URL'])
            ldap_conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
            ldap_conn.set_option(ldap.OPT_REFERRALS, 0)

            today_full = datetime.today().strftime("%Y-%m-%d %H:%M:%S")

            # ── Head Office Login ──────────────────────────────────────
            sql_ho = """
                SELECT username, email, active, role, dept
                FROM dbo.portal_users WITH (NOLOCK)
                WHERE username = ?
            """
            user = MIS_SysDev_cursor.execute(sql_ho, (username,)).fetchone()

            if user:
                if user[2] != 1:
                    flash("SDR Portal Login is deactivated!")
                    return redirect(url_for('index'))

                try:
                    ldap_conn.simple_bind_s(f"MGROUP\\{username}", password)
                except ldap.INVALID_CREDENTIALS:
                    flash("Invalid Head Office Domain Credentials.")
                    return redirect(url_for('index'))

                session.update({
                    'sdr_curr_user_username': user[0].upper(),
                    'username':               user[0].upper(),
                    'sdr_curr_user_role':     user[3],
                    'sdr_loggedin':           True,
                    'sdr_usertype':           'Head Office',
                })

                print(f"{today_full} - {session['sdr_usertype']} - {session['sdr_curr_user_username']}")

                if next_page and is_safe_url(next_page):
                    return redirect(next_page)
                return redirect(url_for('home'))

            # ── Store / BCC Login ──────────────────────────────────────
            sql_store = """
                SELECT bcc, domain_username, store_name, company, ante_date
                FROM portal_store_users WITH (NOLOCK)
                WHERE bcc = ?
            """
            store = MIS_SysDev_cursor.execute(sql_store, (username,)).fetchone()

            if not store:
                flash("Invalid Login - Username does not exist!")
                return redirect(url_for('index'))

            domain_user = store[1]
            ante_days   = store[4]

            try:
                ldap_conn.simple_bind_s(f"MGROUP\\{domain_user}", password)
            except ldap.INVALID_CREDENTIALS:
                flash("Invalid Store Domain Credentials.")
                return redirect(url_for('index'))

            # FIX 2: Convert date to ISO string before storing in session.
            # date objects are not JSON-serialisable and will raise TypeError
            # when Flask tries to sign the session cookie.
            ante_date_obj = date.today() - timedelta(days=ante_days)

            session.update({
                'sdr_curr_user_username': username.upper(),
                'username':               username.upper(),
                'sdr_curr_user_company':  store[3],
                'sdr_curr_user_role':     '',
                'sdr_loggedin':           True,
                'sdr_usertype':           'Store',
                'ante_date_int':          ante_days,
                'ante_date':              ante_date_obj.isoformat(),  # e.g. "2025-03-01"
            })

            print(f"{today_full} - {session['sdr_usertype']} - {session['sdr_curr_user_username']}")

            if next_page and is_safe_url(next_page):
                return redirect(next_page)
            return redirect(url_for('home'))

        except pyodbc.Error as e:
            print(f"DB Error: {e}")
            flash("Database Connectivity Error. Please contact IT.")
            return redirect(url_for('index'))

        except ldap.LDAPError as e:
            print(f"LDAP Error: {e}")
            flash("Active Directory Connectivity Error.")
            return redirect(url_for('index'))

        except Exception as e:
            print(f"System Error: {e}")
            flash("An unexpected System Error occurred.")
            return redirect(url_for('index'))

        finally:
            # Always close connections to prevent resource leaks
            if MIS_SysDev_cursor:
                MIS_SysDev_cursor.close()
            if MIS_SysDev_connect:
                MIS_SysDev_connect.close()
            if ldap_conn:
                try:
                    ldap_conn.unbind_s()
                except Exception:
                    pass

    return render_template('home.html')

# ==========================================
# HOME (post-login landing page)
# ==========================================
@app.route('/home')
@loggedin_required()
def home():
    return render_template('home.html')

# ==========================================
# LOGOUT
# ==========================================
@app.route('/logout')
@loggedin_required()
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run()