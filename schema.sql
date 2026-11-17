CREATE TABLE IF NOT EXISTS departments(id SERIAL PRIMARY KEY, name_en TEXT NOT NULL, name_om TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(id SERIAL PRIMARY KEY, full_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN('admin','manager','leader','officer')),
 department_id INT REFERENCES departments(id), active BOOL DEFAULT TRUE,
 country TEXT, state TEXT, city_county TEXT, district TEXT, job_type TEXT, office_working TEXT,
 phone_number TEXT, profile_photo TEXT, fan_number TEXT UNIQUE,
 approval_status TEXT NOT NULL DEFAULT 'approved' CHECK(approval_status IN('pending','approved','rejected')));
ALTER TABLE users ADD COLUMN IF NOT EXISTS country TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS state TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS city_county TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS district TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS job_type TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS office_working TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_photo TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS fan_number TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT 'approved';
CREATE UNIQUE INDEX IF NOT EXISTS users_fan_number_unique ON users(fan_number) WHERE fan_number IS NOT NULL;
CREATE TABLE IF NOT EXISTS annual_plans(id SERIAL PRIMARY KEY, year INT NOT NULL, title TEXT NOT NULL,
 department_id INT REFERENCES departments(id), status TEXT DEFAULT 'draft' CHECK(status IN('draft','submitted','approved','rejected')),
 created_by INT REFERENCES users(id));
CREATE TABLE IF NOT EXISTS activities(id SERIAL PRIMARY KEY, plan_id INT REFERENCES annual_plans(id) ON DELETE CASCADE,
 title TEXT NOT NULL, unit TEXT DEFAULT 'count', annual_target NUMERIC NOT NULL DEFAULT 0,
 weight NUMERIC NOT NULL DEFAULT 0 CHECK(weight>=0),
 assigned_to INT REFERENCES users(id), created_by INT REFERENCES users(id));
ALTER TABLE activities ADD COLUMN IF NOT EXISTS weight NUMERIC NOT NULL DEFAULT 0 CHECK(weight>=0);
CREATE TABLE IF NOT EXISTS period_targets(id SERIAL PRIMARY KEY,
 activity_id INT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
 period_type TEXT NOT NULL CHECK(period_type IN('monthly','quarterly','weekly','daily')),
 period_start DATE NOT NULL, target NUMERIC NOT NULL CHECK(target>=0),
 created_by INT REFERENCES users(id),
 UNIQUE(activity_id,period_type,period_start));
ALTER TABLE period_targets DROP CONSTRAINT IF EXISTS period_targets_period_type_check;
ALTER TABLE period_targets ADD CONSTRAINT period_targets_period_type_check
 CHECK(period_type IN('monthly','quarterly','weekly','daily'));
CREATE TABLE IF NOT EXISTS daily_performances(id SERIAL PRIMARY KEY, activity_id INT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
 perf_date DATE NOT NULL, quantity NUMERIC NOT NULL CHECK(quantity>=0), notes TEXT,
 status TEXT DEFAULT 'pending' CHECK(status IN('pending','approved','rejected')),
 submitted_by INT REFERENCES users(id), UNIQUE(activity_id, perf_date));  -- one entry per task per day: no double counting
CREATE TABLE IF NOT EXISTS evidences(id SERIAL PRIMARY KEY, daily_id INT REFERENCES daily_performances(id) ON DELETE CASCADE, filename TEXT, path TEXT);
CREATE TABLE IF NOT EXISTS approvals(id SERIAL PRIMARY KEY, entity TEXT, entity_id INT, decision TEXT, comment TEXT,
 decided_by INT REFERENCES users(id), decided_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS audit_logs(id SERIAL PRIMARY KEY, user_id INT, action TEXT, detail TEXT, at TIMESTAMPTZ DEFAULT now());
-- Weekly/monthly/quarterly/annual are always derived from approved daily rows
CREATE OR REPLACE VIEW weekly_performances AS SELECT activity_id, date_trunc('week',perf_date)::date AS week_start, SUM(quantity) AS total FROM daily_performances WHERE status='approved' GROUP BY 1,2;
CREATE OR REPLACE VIEW monthly_performances AS SELECT activity_id, EXTRACT(year FROM perf_date)::int AS year, EXTRACT(month FROM perf_date)::int AS month, SUM(quantity) AS total FROM daily_performances WHERE status='approved' GROUP BY 1,2,3;
CREATE OR REPLACE VIEW quarterly_performances AS SELECT activity_id, EXTRACT(year FROM perf_date)::int AS year, EXTRACT(quarter FROM perf_date)::int AS quarter, SUM(quantity) AS total FROM daily_performances WHERE status='approved' GROUP BY 1,2,3;
CREATE OR REPLACE VIEW annual_performances AS SELECT a.id AS activity_id, a.title, p.year, a.annual_target, COALESCE(SUM(d.quantity),0) AS total,
 ROUND(100*COALESCE(SUM(d.quantity),0)/NULLIF(a.annual_target,0),1) AS pct
 FROM activities a JOIN annual_plans p ON p.id=a.plan_id
 LEFT JOIN daily_performances d ON d.activity_id=a.id AND d.status='approved' AND EXTRACT(year FROM d.perf_date)=p.year GROUP BY a.id,a.title,p.year,a.annual_target;
