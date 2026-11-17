import os, io, psycopg2, psycopg2.extras
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import uuid
from functools import wraps
from flask import Flask, g, render_template, request, redirect, url_for, session, send_file, send_from_directory, jsonify, abort
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from i18n import T

app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY', 'change-me'), MAX_CONTENT_LENGTH=10*1024*1024,
                  SESSION_COOKIE_SECURE=bool(os.environ.get('PROD')), SESSION_COOKIE_HTTPONLY=True)
DSN = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost/itpms')
UP = os.path.join(os.path.dirname(__file__), 'uploads')
lm = LoginManager(app); lm.login_view = 'login'

def db():
    if 'db' not in g: g.db = psycopg2.connect(DSN, cursor_factory=psycopg2.extras.RealDictCursor)
    return g.db
@app.teardown_appcontext
def close(_):
    c = g.pop('db', None)
    if c: c.close()
def q(sql, a=(), one=False, w=False):
    with db().cursor() as c:
        c.execute(sql, a)
        r = (c.fetchone() if one else c.fetchall()) if c.description else None
    if w: db().commit()
    return r
def audit(act, detail=''):
    q("INSERT INTO audit_logs(user_id,action,detail) VALUES(%s,%s,%s)", (current_user.id, act, detail), w=True)

def ensure_tables():
    try:
        q("""CREATE TABLE IF NOT EXISTS period_targets(
                id SERIAL PRIMARY KEY,
                activity_id INT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
                period_type TEXT NOT NULL CHECK(period_type IN('monthly','quarterly','weekly','daily')),
                period_start DATE NOT NULL,
                target NUMERIC NOT NULL CHECK(target>=0),
                created_by INT REFERENCES users(id),
                UNIQUE(activity_id,period_type,period_start))""", w=True)
    except Exception:
        pass
    try:
        q("ALTER TABLE activities ADD COLUMN IF NOT EXISTS past_year_performance NUMERIC NOT NULL DEFAULT 0", w=True)
    except Exception:
        pass
    try:
        q("ALTER TABLE daily_performances ADD COLUMN IF NOT EXISTS is_draft BOOLEAN NOT NULL DEFAULT FALSE", w=True)
    except Exception:
        pass
    try:
        q("ALTER TABLE period_targets ADD COLUMN IF NOT EXISTS achieved NUMERIC NOT NULL DEFAULT 0", w=True)
    except Exception:
        pass
    for stmt in [
        "ALTER TABLE annual_plans ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'draft'",
        "ALTER TABLE activities ADD COLUMN IF NOT EXISTS weight NUMERIC NOT NULL DEFAULT 0",
        "ALTER TABLE activities ADD COLUMN IF NOT EXISTS unit TEXT DEFAULT 'count'",
    ]:
        try:
            q(stmt, w=True)
        except Exception:
            pass

class U(UserMixin):
    def __init__(s, r): s.id, s.name, s.role, s.dept = r['id'], r['full_name'], r['role'], r['department_id']
@lm.user_loader
def load(i):
    r = q("SELECT * FROM users WHERE id=%s AND active AND approval_status='approved'", (i,), one=True); return U(r) if r else None
def roles(*rs):
    def d(f):
        @wraps(f)
        @login_required
        def w(*a, **k):
            if current_user.role not in rs: abort(403)
            return f(*a, **k)
        return w
    return d
@app.context_processor
def ctx(): return dict(t=T[session.get('lang', 'en')], lang=session.get('lang', 'en'))
@app.route('/lang/<l>')
def lang(l):
    if l in T: session['lang'] = l
    return redirect(request.referrer or url_for('home'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    err = None
    if request.method == 'POST':
        f = request.form
        photo = request.files.get('profile_photo')
        fields = ('full_name', 'country', 'state', 'city_county', 'district', 'job_type',
                  'office_working', 'phone_number', 'email', 'fan_number', 'password', 'confirm_password')
        if any(not f.get(key, '').strip() for key in fields):
            err = 'Complete all required fields.'
        elif f['password'] != f['confirm_password']:
            err = 'Passwords do not match.'
        elif len(f['password']) < 8:
            err = 'Password must be at least 8 characters.'
        elif not photo or not photo.filename:
            err = 'A profile photo is required.'
        elif os.path.splitext(photo.filename)[1].lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
            err = 'Profile photo must be JPG, PNG, or WEBP.'
        else:
            email = f['email'].strip().lower()
            fan = f['fan_number'].strip()
            exists = q("SELECT id FROM users WHERE email=%s OR fan_number=%s", (email, fan), one=True)
            if exists:
                err = 'That email or FAN number is already registered.'
            else:
                os.makedirs(UP, exist_ok=True)
                photo_name = f"profile_{uuid.uuid4().hex}{os.path.splitext(photo.filename)[1].lower()}"
                photo.save(os.path.join(UP, photo_name))
                q("""INSERT INTO users(full_name,email,password_hash,role,active,country,state,city_county,
                     district,job_type,office_working,phone_number,profile_photo,fan_number,approval_status)
                     VALUES(%s,%s,%s,'officer',FALSE,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')""",
                  (f['full_name'].strip(), email, generate_password_hash(f['password']), f['country'].strip(),
                   f['state'].strip(), f['city_county'].strip(), f['district'].strip(), f['job_type'].strip(),
                   f['office_working'].strip(), f['phone_number'].strip(), photo_name, fan), w=True)
                return render_template('register.html', submitted=True)
    return render_template('register.html', err=err, submitted=False)

@app.route('/uploads/<path:name>')
@login_required
def uploaded_file(name):
    return send_from_directory(UP, name)

@app.route('/login', methods=['GET', 'POST'])
def login():
    err = False
    if request.method == 'POST':
        r = q("SELECT * FROM users WHERE email=%s AND active AND approval_status='approved'", (request.form['email'].lower(),), one=True)
        if r and check_password_hash(r['password_hash'], request.form['password']):
            login_user(U(r)); audit('login'); return redirect(url_for('home'))
        err = True
    return render_template('login.html', err=err)
@app.route('/logout')
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    kpi = q("SELECT * FROM annual_performances WHERE year=EXTRACT(year FROM now())")
    pend = q("SELECT COUNT(*) n FROM daily_performances WHERE status='pending'", one=True)['n']
    return render_template('dashboard.html', kpi=kpi, pend=pend)
@app.route('/api/monthly')
@login_required
def api_monthly():
    rows = q("SELECT month, SUM(total) s FROM monthly_performances WHERE year=EXTRACT(year FROM now()) GROUP BY month ORDER BY month")
    return jsonify(labels=[r['month'] for r in rows], data=[float(r['s']) for r in rows])

@app.route('/planning', methods=['GET', 'POST'])
@login_required
def planning():
    ensure_tables()
    f = request.form
    if request.method == 'POST':
        if f['do'] == 'plan' and current_user.role in ('officer', 'leader', 'manager'):
            q("INSERT INTO annual_plans(year,title,department_id,created_by) VALUES(%s,%s,%s,%s)", (f['year'], f['title'], current_user.dept, current_user.id), w=True)
        elif f['do'] == 'activity' and current_user.role in ('officer', 'leader', 'manager'):
            plan = q("SELECT id,created_by,status FROM annual_plans WHERE id=%s", (f['plan_id'],), one=True)
            if not plan or plan['status'] == 'approved' or (plan['created_by'] != current_user.id and current_user.role != 'leader'):
                abort(403)
            q("INSERT INTO activities(plan_id,title,unit,annual_target,weight,past_year_performance,assigned_to,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
              (f['plan_id'], f['title'], f.get('unit', 'count'), f['target'], f.get('weight') or 0, f.get('past_year_performance') or 0,
               f.get('assigned_to') if current_user.role == 'leader' and f.get('assigned_to') else current_user.id, current_user.id), w=True)
        elif f['do'] == 'decide' and current_user.role == 'manager':
            if f['decision'] not in ('approved', 'rejected'):
                abort(400)
            q("UPDATE annual_plans SET status=%s WHERE id=%s AND status='submitted'", (f['decision'], f['id']), w=True)
            q("INSERT INTO approvals(entity,entity_id,decision,decided_by) VALUES('plan',%s,%s,%s)", (f['id'], f['decision'], current_user.id), w=True)
        elif f['do'] == 'submit':
            plan = q("SELECT id,created_by,status FROM annual_plans WHERE id=%s", (f['id'],), one=True)
            if not plan or plan['status'] not in ('draft', 'rejected') or (plan['created_by'] != current_user.id and current_user.role != 'leader'):
                abort(403)
            q("UPDATE annual_plans SET status='submitted' WHERE id=%s", (f['id'],), w=True)
        elif f['do'] == 'period_target' and current_user.role in ('officer', 'leader', 'manager'):
            period_type = f.get('period_type')
            try:
                activity_id = int(f['activity_id'])
                target = Decimal(f['target'])
                if not target.is_finite() or target < 0:
                    abort(400)
                if period_type == 'monthly':
                    year, month = int(f['year']), int(f['month'])
                    period_start = date(year, month, 1)
                elif period_type == 'quarterly':
                    year, quarter = int(f['year']), int(f['quarter'])
                    if quarter not in (1, 2, 3, 4):
                        abort(400)
                    period_start = date(year, 1 + (quarter - 1) * 3, 1)
                elif period_type in ('weekly', 'daily'):
                    period_start = datetime.strptime(f['period_start'], '%Y-%m-%d').date()
                    if period_type == 'weekly':
                        period_start -= timedelta(days=period_start.weekday())
                else:
                    abort(400)
            except (KeyError, ValueError, InvalidOperation):
                abort(400)
            activity = q("SELECT id,assigned_to FROM activities WHERE id=%s", (activity_id,), one=True)
            if not activity or (current_user.role == 'officer' and activity['assigned_to'] != current_user.id):
                abort(403)
            q("""INSERT INTO period_targets(activity_id,period_type,period_start,target,created_by)
                 VALUES(%s,%s,%s,%s,%s)
                 ON CONFLICT(activity_id,period_type,period_start)
                 DO UPDATE SET target=EXCLUDED.target,created_by=EXCLUDED.created_by""",
              (activity_id, period_type, period_start, target, current_user.id), w=True)
        audit('planning', f['do']); return redirect(url_for('planning', period=f.get('view', 'annual')))
    period = request.args.get('period', 'annual')
    if period not in ('annual', 'monthly', 'quarterly', 'weekly', 'daily'):
        period = 'annual'
    return render_template('planning.html', plans=q("SELECT * FROM annual_plans ORDER BY year DESC,id DESC"),
        acts=q("""SELECT a.*,u.full_name who,
            COALESCE(NULLIF(a.past_year_performance,0), (SELECT SUM(d.quantity) FROM daily_performances d
                WHERE d.activity_id=a.id AND d.status='approved'
                AND EXTRACT(year FROM d.perf_date)=p.year-1), 0) AS past_year_performance
            FROM activities a JOIN annual_plans p ON p.id=a.plan_id
            LEFT JOIN users u ON u.id=a.assigned_to ORDER BY a.id"""),
        staff=q("SELECT id,full_name FROM users WHERE role='officer' AND active"),
        target_acts=q("""SELECT a.id,a.title,a.unit,a.annual_target FROM activities a
            WHERE (%s IN ('manager','leader') OR a.assigned_to=%s)
            ORDER BY a.title""", (current_user.role, current_user.id)),
        period_targets=q("""SELECT pt.*,a.title,a.unit,a.annual_target,a.weight,u.full_name AS assigned_to,
            COALESCE((SELECT SUM(d.quantity) FROM daily_performances d
                WHERE d.activity_id=a.id AND d.status='approved'
                AND EXTRACT(year FROM d.perf_date)=EXTRACT(year FROM pt.period_start)::int-1),0) AS past_year_performance
            FROM period_targets pt JOIN activities a ON a.id=pt.activity_id
            LEFT JOIN users u ON u.id=a.assigned_to
            ORDER BY pt.period_start DESC,a.title"""),
        period=period, current_year=date.today().year)

@app.route('/performance', methods=['GET', 'POST'])
@roles('officer', 'leader')
def performance():
    ensure_tables()
    if request.method == 'POST':
        f = request.form
        if f.get('do') == 'achieved':
            view = f.get('view', 'monthly')
            if view not in ('monthly', 'quarterly', 'weekly', 'daily'):
                abort(400)
            for key in f:
                if not key.startswith('achieved_'):
                    continue
                raw = (f.get(key) or '').strip()
                if raw == '':
                    continue
                try:
                    pid = int(key[len('achieved_'):])
                    value = Decimal(raw)
                except (ValueError, InvalidOperation):
                    abort(400)
                if not value.is_finite() or value < 0:
                    abort(400)
                q("""UPDATE period_targets pt SET achieved=%s
                     FROM activities a
                     WHERE a.id=pt.activity_id AND pt.id=%s AND pt.period_type=%s
                     AND (%s=FALSE OR a.assigned_to=%s)""",
                  (value, pid, view, current_user.role == 'officer', current_user.id), w=True)
            audit('period_achieved', view)
            return redirect(url_for('performance', period=view))
        if not f.get('activity_id'):
            abort(400)
        action = f.get('action', 'submit')
        status = 'draft' if action == 'draft' else 'pending'
        is_draft = action == 'draft'
        r = q("""INSERT INTO daily_performances(activity_id,perf_date,quantity,notes,submitted_by,status,is_draft)
                 VALUES(%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT(activity_id,perf_date) DO UPDATE SET quantity=EXCLUDED.quantity,notes=EXCLUDED.notes,status=EXCLUDED.status,is_draft=EXCLUDED.is_draft RETURNING id""",
              (f['activity_id'], f['perf_date'], f['quantity'], f['notes'], current_user.id, status, is_draft), one=True, w=True)
        file = request.files.get('evidence')
        if file and file.filename:
            n = f"{r['id']}_{secure_filename(file.filename)}"; file.save(os.path.join(UP, n))
            q("INSERT INTO evidences(daily_id,filename,path) VALUES(%s,%s,%s)", (r['id'], file.filename, n), w=True)
        audit('daily_entry_draft' if is_draft else 'daily_entry', str(r['id']))
        if action == 'submit':
            return redirect(url_for('reports', period='performance'))
        return redirect(url_for('performance'))
    period = request.args.get('period', 'daily')
    if period not in ('annual', 'monthly', 'quarterly', 'weekly', 'daily'):
        period = 'daily'
    scope = (current_user.role == 'officer', current_user.id)
    period_rows = []
    if period != 'annual':
        period_rows = q("""SELECT pt.id,pt.period_type,pt.period_start,pt.target,pt.achieved,
            a.title,a.unit,a.weight,a.annual_target,u.full_name AS assigned_to,
            COALESCE((SELECT SUM(d.quantity) FROM daily_performances d
                WHERE d.activity_id=a.id AND d.status='approved'
                AND EXTRACT(year FROM d.perf_date)=EXTRACT(year FROM pt.period_start)::int-1),0) AS past_year_performance
            FROM period_targets pt JOIN activities a ON a.id=pt.activity_id
            LEFT JOIN users u ON u.id=a.assigned_to
            WHERE pt.period_type=%s AND (%s=FALSE OR a.assigned_to=%s)
            ORDER BY pt.period_start DESC,a.title LIMIT 500""",
            (period, current_user.role == 'officer', current_user.id))
    return render_template('performance.html', period=period,
        acts=q("""SELECT a.id,a.title FROM activities a JOIN annual_plans p ON p.id=a.plan_id
            WHERE p.status='approved' AND (%s=FALSE OR a.assigned_to=%s) ORDER BY a.title""", scope),
        annual=q("""SELECT a.id AS activity_id,a.title,p.year,a.annual_target,a.unit,a.weight,
            COALESCE(SUM(pt.achieved),0) AS total,
            ROUND(100*COALESCE(SUM(pt.achieved),0)/NULLIF(a.annual_target,0),1) AS pct,
            COALESCE((SELECT SUM(d.quantity) FROM daily_performances d
                WHERE d.activity_id=a.id AND d.status='approved'
                AND EXTRACT(year FROM d.perf_date)=p.year-1),0) AS past_year_performance
            FROM activities a JOIN annual_plans p ON p.id=a.plan_id
            LEFT JOIN period_targets pt ON pt.activity_id=a.id AND pt.period_type='monthly'
                AND EXTRACT(year FROM pt.period_start)=p.year
            WHERE (%s=FALSE OR a.assigned_to=%s)
            GROUP BY a.id,a.title,p.year,a.annual_target,a.unit,a.weight
            ORDER BY p.year DESC,a.title""", scope),
        period_rows=period_rows)

@app.route('/approvals', methods=['GET', 'POST'])
@roles('leader', 'manager')
def approvals():
    if request.method == 'POST':
        d = 'approved' if request.form['decision'] == 'approve' else 'rejected'
        q("UPDATE daily_performances SET status=%s WHERE id=%s", (d, request.form['id']), w=True)
        q("INSERT INTO approvals(entity,entity_id,decision,decided_by) VALUES('daily',%s,%s,%s)", (request.form['id'], d, current_user.id), w=True)
        audit('approval', f"{request.form['id']}:{d}"); return redirect(url_for('approvals'))
    return render_template('approvals.html', rows=q("SELECT d.*,a.title,u.full_name who FROM daily_performances d JOIN activities a ON a.id=d.activity_id JOIN users u ON u.id=d.submitted_by WHERE d.status='pending' ORDER BY perf_date"))

@app.route('/users', methods=['GET', 'POST'])
@roles('admin')
def users():
        if request.method == 'POST':
                f = request.form
                if f.get('action') == 'approval':
                        decision = f.get('decision')
                        if decision not in ('approved', 'rejected'):
                                abort(400)
                        q("UPDATE users SET approval_status=%s,active=%s WHERE id=%s AND role!='admin'",
                            (decision, decision == 'approved', f['user_id']), w=True)
                        audit('registration_' + decision, f['user_id'])
                        return redirect(url_for('users'))
                q("INSERT INTO users(full_name,email,password_hash,role,department_id) VALUES(%s,%s,%s,%s,%s)",
                    (f['name'], f['email'].lower(), generate_password_hash(f['password']), f['role'], f.get('dept') or None), w=True)
                audit('user_add', f['email']); return redirect(url_for('users'))
        return render_template('users.html', rows=q("SELECT u.*,d.name_en FROM users u LEFT JOIN departments d ON d.id=u.department_id ORDER BY CASE WHEN u.approval_status='pending' THEN 0 ELSE 1 END,u.id"), depts=q("SELECT * FROM departments"))

@app.route('/settings', methods=['GET', 'POST'])
@roles('admin')
def settings():
    if request.method == 'POST':
        q("INSERT INTO departments(name_en,name_om) VALUES(%s,%s)", (request.form['en'], request.form['om']), w=True); return redirect(url_for('settings'))
    return render_template('settings.html', depts=q("SELECT * FROM departments"), logs=q("SELECT l.*,u.full_name FROM audit_logs l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 50"))

@app.route('/reports')
@roles('admin', 'manager', 'leader', 'officer')
def reports():
    period = request.args.get('period', 'performance')
    if period not in ('performance', 'annual', 'monthly', 'quarterly', 'weekly', 'daily'):
        period = 'performance'
    assigned_only = current_user.role == 'officer'
    scope = (assigned_only, current_user.id)
    return render_template('reports.html', period=period,
        overall=q("""SELECT a.id,a.title,a.unit,
            COALESCE(SUM(d.quantity) FILTER (WHERE d.status='approved'),0) AS total,
            COUNT(d.id) FILTER (WHERE d.status='approved') AS entries
            FROM activities a JOIN annual_plans p ON p.id=a.plan_id
            LEFT JOIN daily_performances d ON d.activity_id=a.id
            WHERE p.status='approved' AND (%s=FALSE OR a.assigned_to=%s)
            GROUP BY a.id ORDER BY a.title""", scope),
        annual=q("""SELECT r.* FROM annual_performances r JOIN activities a ON a.id=r.activity_id
            WHERE (%s=FALSE OR a.assigned_to=%s) ORDER BY r.year DESC,r.title""", scope),
        monthly=q("""SELECT m.*,a.title,a.unit FROM monthly_performances m
            JOIN activities a ON a.id=m.activity_id WHERE (%s=FALSE OR a.assigned_to=%s)
            ORDER BY m.year DESC,m.month DESC,a.title LIMIT 200""", scope),
        quarterly=q("""SELECT q.*,a.title,a.unit FROM quarterly_performances q
            JOIN activities a ON a.id=q.activity_id WHERE (%s=FALSE OR a.assigned_to=%s)
            ORDER BY q.year DESC,q.quarter DESC,a.title LIMIT 200""", scope),
        weekly=q("""SELECT w.*,a.title,a.unit FROM weekly_performances w
            JOIN activities a ON a.id=w.activity_id WHERE (%s=FALSE OR a.assigned_to=%s)
            ORDER BY w.week_start DESC,a.title LIMIT 200""", scope),
        daily=q("""SELECT d.*,a.title,a.unit,u.full_name AS who FROM daily_performances d
            JOIN activities a ON a.id=d.activity_id LEFT JOIN users u ON u.id=d.submitted_by
            WHERE (%s=FALSE OR a.assigned_to=%s)
            ORDER BY d.perf_date DESC,a.title LIMIT 200""", scope))
@app.route('/export/<fmt>')
@roles('admin', 'manager', 'leader')
def export(fmt):
    rows = q("SELECT year,title,annual_target,total,pct FROM annual_performances ORDER BY year DESC,title")
    head = ['Year', 'Activity', 'Target', 'Achieved', '%']; data = [[r['year'], r['title'], float(r['annual_target']), float(r['total']), float(r['pct'] or 0)] for r in rows]
    b = io.BytesIO()
    if fmt == 'xlsx':
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active; ws.append(head); [ws.append(r) for r in data]; wb.save(b)
        mime, n = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'itpms.xlsx'
    else:
        from reportlab.lib.pagesizes import A4; from reportlab.platypus import SimpleDocTemplate, Table, TableStyle; from reportlab.lib import colors
        tb = Table([head] + data); tb.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e6b4a')), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('GRID', (0, 0), (-1, -1), .3, colors.grey)]))
        SimpleDocTemplate(b, pagesize=A4).build([tb]); mime, n = 'application/pdf', 'itpms.pdf'
    b.seek(0); audit('export', fmt); return send_file(b, mimetype=mime, as_attachment=True, download_name=n)

@app.cli.command('init-db')
def init_db():
    with psycopg2.connect(DSN) as c, c.cursor() as k:
        k.execute(open(os.path.join(os.path.dirname(__file__), 'schema.sql')).read())
        k.execute("INSERT INTO departments(name_en,name_om) SELECT 'ICT Directorate','Daayrektoreetii ICT' WHERE NOT EXISTS(SELECT 1 FROM departments)")
        k.execute("INSERT INTO users(full_name,email,password_hash,role) VALUES('Administrator','admin@itpms.local',%s,'admin') ON CONFLICT DO NOTHING", (generate_password_hash('Admin@123'),))
    print('Done. Login admin@itpms.local / Admin@123 (change it now)')
