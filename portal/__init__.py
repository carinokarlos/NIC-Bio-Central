import os
from flask import Flask, flash, redirect, url_for
from functools import update_wrapper
from flask import session


def loggedin_required():
    def decorator(fn):
        def wrapped_function(*args, **kwargs):
            if 'sdr_loggedin' not in session or not session['sdr_loggedin']:
                flash("Please login to access this page")
                return redirect(url_for('index',_external=True,))
            return fn(*args, **kwargs)
        return update_wrapper(wrapped_function, fn)
    return decorator


def require_role(role_code):
    def decorator(fn):
        def wrapped_function(*args, **kwargs):
            if 'sdr_loggedin' not in session or not session['sdr_loggedin']:
                return redirect(url_for('index',_external=True,))
            if role_code+";" not in session['sdr_curr_user_role']:
                flash("You are not authorized to access this page")
                return redirect(url_for('index',_external=True,))
            return fn(*args, **kwargs)
        return update_wrapper(wrapped_function, fn)
    return decorator


def require_type(dept_code):
    def decorator(fn):
        def wrapped_function(*args, **kwargs):
            if 'sdr_loggedin' not in session or not session['sdr_loggedin']:
                return redirect(url_for('index',_external=True,))
            if dept_code not in session['sdr_usertype']:
                flash("You are not authorized to access this page")
                return redirect(url_for('index',_external=True,))
            return fn(*args, **kwargs)
        return update_wrapper(wrapped_function, fn)
    return decorator


# 2. Create the flask app
app = Flask(__name__)


app.config['SECRET_KEY']        = 'n3wtr3nds!'
app.config['UPLOAD_FOLDER'] = os.path.abspath(os.path.dirname(__file__))+'/uploads'
app.config['LDAP_PROVIDER_URL'] = 'ldap://MGSVR01.mgroup.local/'
app.config['ATC_NAV']           = 'DRIVER={SQL Server};SERVER=MGSVR14.mgroup.local;DATABASE=ATCREP;UID=nav;trusted_connection=yes;READONLY=True;'
app.config['NIC_NAV']           ='DRIVER={SQL Server};SERVER=MGSVR14.mgroup.local;DATABASE=nicrep;UID=nav;trusted_connection=yes;READONLY=True;'
app.config['MIS_SysDev']        = 'DRIVER={SQL Server};SERVER=MGSVR14.mgroup.local;DATABASE=MIS_SysDev;UID=nicportal;PWD=n1cp0rtal;READONLY=True;'

app.config['MIS_SysDev_connect'] = 'MIS_SysDev'

from routes.store_crud import store_crud_bp
app.register_blueprint(store_crud_bp)

from routes.audit_log import audit_log_bp
app.register_blueprint(audit_log_bp)

from routes.device_sync import sync_time_bp
app.register_blueprint(sync_time_bp)

from routes.get_employees import get_employees_bp
app.register_blueprint(get_employees_bp)

from routes.user_enrollment import enroll_bp
app.register_blueprint(enroll_bp)

from routes.move_registration import move_registration_bp 
app.register_blueprint(move_registration_bp)