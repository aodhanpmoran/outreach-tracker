from flask import Flask, render_template, request, jsonify
import sqlite3
from datetime import datetime, date, timedelta
import os

app = Flask(__name__, template_folder='public')
DB_PATH = os.path.join(os.path.dirname(__file__), 'outreach.db')

PIPELINE_STATUSES = {'contacted', 'responded', 'call_scheduled', 'closed'}
NOISE_STATUSES = {'new', 'lost', 'pilot', 'client'}
ACTIVE_DEAL_STATUSES = PIPELINE_STATUSES
REQUIRED_ACTIVE_FIELDS = ['next_action', 'next_action_due_date', 'action_channel', 'action_objective']


def validate_prospect_payload(data, target_status=None):
    status = target_status or data.get('status', 'new')

    if status in ACTIVE_DEAL_STATUSES:
        missing = []
        for field in REQUIRED_ACTIVE_FIELDS:
            value = data.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(field)
        if missing:
            return f"Missing required fields for active deal: {', '.join(missing)}"

    return None


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS prospects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            company TEXT,
            email TEXT,
            linkedin TEXT,
            notes TEXT,
            status TEXT DEFAULT 'new',
            next_followup DATE,
            next_action TEXT,
            next_action_due_date DATE,
            action_channel TEXT,
            action_objective TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Safe additive migration for existing databases
    for ddl in [
        "ALTER TABLE prospects ADD COLUMN next_action TEXT",
        "ALTER TABLE prospects ADD COLUMN next_action_due_date DATE",
        "ALTER TABLE prospects ADD COLUMN action_channel TEXT",
        "ALTER TABLE prospects ADD COLUMN action_objective TEXT"
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass

    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            completed_at TIMESTAMP,
            date_entered DATE DEFAULT (date('now')),
            date_scheduled DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/prospects', methods=['GET'])
def get_prospects():
    fields_param = request.args.get('fields')
    allowed_fields = {
        'id', 'name', 'company', 'email', 'linkedin', 'notes', 'status',
        'next_followup', 'next_action', 'next_action_due_date', 'action_channel', 'action_objective',
        'created_at', 'updated_at'
    }
    if fields_param:
        requested = [f.strip() for f in fields_param.split(',') if f.strip()]
        selected = [f for f in requested if f in allowed_fields]
        select_fields = ', '.join(selected) if selected else '*'
    else:
        select_fields = '*'

    conn = get_db()
    prospects = conn.execute(
        f'SELECT {select_fields} FROM prospects ORDER BY updated_at DESC'
    ).fetchall()
    conn.close()
    return jsonify([dict(p) for p in prospects])

@app.route('/api/prospects', methods=['POST'])
def add_prospect():
    data = request.json
    validation_error = validate_prospect_payload(data)
    if validation_error:
        return jsonify({'error': validation_error}), 400

    conn = get_db()
    cursor = conn.execute('''
        INSERT INTO prospects (
            name, company, email, linkedin, notes, status, next_followup,
            next_action, next_action_due_date, action_channel, action_objective
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('name'),
        data.get('company'),
        data.get('email'),
        data.get('linkedin'),
        data.get('notes'),
        data.get('status', 'new'),
        data.get('next_followup'),
        data.get('next_action'),
        data.get('next_action_due_date'),
        data.get('action_channel'),
        data.get('action_objective')
    ))
    conn.commit()
    prospect_id = cursor.lastrowid
    prospect = conn.execute('SELECT * FROM prospects WHERE id = ?', (prospect_id,)).fetchone()
    conn.close()
    return jsonify(dict(prospect))

@app.route('/api/prospects/<int:id>', methods=['PUT'])
def update_prospect(id):
    data = request.json
    validation_error = validate_prospect_payload(data)
    if validation_error:
        return jsonify({'error': validation_error}), 400

    conn = get_db()
    conn.execute('''
        UPDATE prospects
        SET name = ?, company = ?, email = ?, linkedin = ?, notes = ?,
            status = ?, next_followup = ?, next_action = ?, next_action_due_date = ?,
            action_channel = ?, action_objective = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (
        data.get('name'),
        data.get('company'),
        data.get('email'),
        data.get('linkedin'),
        data.get('notes'),
        data.get('status'),
        data.get('next_followup'),
        data.get('next_action'),
        data.get('next_action_due_date'),
        data.get('action_channel'),
        data.get('action_objective'),
        id
    ))
    conn.commit()
    prospect = conn.execute('SELECT * FROM prospects WHERE id = ?', (id,)).fetchone()
    conn.close()
    return jsonify(dict(prospect))

@app.route('/api/prospects/<int:id>', methods=['DELETE'])
def delete_prospect(id):
    conn = get_db()
    conn.execute('DELETE FROM prospects WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/prospects/<int:id>/status', methods=['PATCH'])
def update_status(id):
    data = request.json
    target_status = data.get('status')

    conn = get_db()
    existing = conn.execute('SELECT * FROM prospects WHERE id = ?', (id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({'error': 'Prospect not found'}), 404

    merged = dict(existing)
    merged.update(data or {})
    validation_error = validate_prospect_payload(merged, target_status=target_status)
    if validation_error:
        conn.close()
        return jsonify({'error': validation_error}), 400

    conn.execute('''
        UPDATE prospects SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
    ''', (target_status, id))
    conn.commit()
    prospect = conn.execute('SELECT * FROM prospects WHERE id = ?', (id,)).fetchone()
    conn.close()
    return jsonify(dict(prospect))

@app.route('/api/prospect', methods=['GET'])
def get_prospect():
    prospect_id = request.args.get('id')
    if not prospect_id:
        return jsonify({'error': 'Missing id parameter'}), 400

    conn = get_db()
    prospect = conn.execute('SELECT * FROM prospects WHERE id = ?', (prospect_id,)).fetchone()
    conn.close()

    if prospect:
        return jsonify(dict(prospect))
    return jsonify(None)

@app.route('/api/stats', methods=['GET'])
def get_stats():
    conn = get_db()
    status_counts = {}
    for row in conn.execute('SELECT status, COUNT(*) as count FROM prospects GROUP BY status').fetchall():
        status_counts[row['status']] = row['count']
    total = conn.execute('SELECT COUNT(*) as count FROM prospects').fetchone()['count']
    conn.close()

    return jsonify({
        'total': total,
        'by_status': status_counts,
        'conversion_rate': round((status_counts.get('closed', 0) / total * 100), 1) if total > 0 else 0,
        'response_rate': round(
            ((status_counts.get('responded', 0) + status_counts.get('call_scheduled', 0) +
              status_counts.get('closed', 0) + status_counts.get('pilot', 0) +
              status_counts.get('client', 0) + status_counts.get('lost', 0)) /
             max(total - status_counts.get('new', 0), 1) * 100), 1
        ) if total > status_counts.get('new', 0) else 0
    })


@app.route('/api/hot-list', methods=['GET'])
def get_hot_list():
    conn = get_db()
    prospects = conn.execute('''
        SELECT * FROM prospects
        WHERE status IN ('contacted', 'responded', 'call_scheduled', 'closed')
        ORDER BY next_action_due_date ASC, updated_at DESC
    ''').fetchall()
    conn.close()

    today = date.today()
    in_14 = today + timedelta(days=14)

    due_today = []
    overdue = []
    closing_14_days = []

    for row in prospects:
        p = dict(row)
        due_raw = p.get('next_action_due_date')
        if due_raw:
            try:
                due = datetime.strptime(due_raw, '%Y-%m-%d').date()
                if due == today:
                    due_today.append(p)
                elif due < today:
                    overdue.append(p)
            except ValueError:
                pass

        close_signal_raw = p.get('next_followup') or p.get('next_action_due_date')
        if close_signal_raw and p.get('status') in {'call_scheduled', 'closed'}:
            try:
                close_signal = datetime.strptime(close_signal_raw, '%Y-%m-%d').date()
                if today <= close_signal <= in_14:
                    closing_14_days.append(p)
            except ValueError:
                pass

    return jsonify({
        'due_today': due_today,
        'overdue': overdue,
        'closing_14_days': closing_14_days
    })

# Tasks API
@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    conn = get_db()
    date_entered = request.args.get('date_entered')
    completed = request.args.get('completed')
    limit_param = request.args.get('limit')
    offset_param = request.args.get('offset')

    query = 'SELECT * FROM tasks'
    params = []
    conditions = []

    if date_entered:
        conditions.append('date_entered = ?')
        params.append(date_entered)
    if completed is not None:
        conditions.append('completed = ?')
        params.append(1 if completed.lower() == 'true' else 0)

    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)

    query += ' ORDER BY date_entered DESC, created_at DESC'

    if limit_param:
        try:
            limit = max(1, min(int(limit_param), 1000))
        except ValueError:
            limit = 200
        try:
            offset = max(0, int(offset_param or 0))
        except ValueError:
            offset = 0
        query += ' LIMIT ? OFFSET ?'
        params.extend([limit, offset])

    tasks = conn.execute(query, params).fetchall()
    conn.close()

    result = []
    for t in tasks:
        task_dict = dict(t)
        task_dict['completed'] = bool(task_dict['completed'])
        result.append(task_dict)

    return jsonify(result)

@app.route('/api/tasks', methods=['POST'])
def add_task():
    data = request.json
    conn = get_db()
    cursor = conn.execute('''
        INSERT INTO tasks (text, completed, date_entered, date_scheduled)
        VALUES (?, ?, ?, ?)
    ''', (
        data.get('text'),
        1 if data.get('completed') else 0,
        data.get('date_entered', datetime.now().strftime('%Y-%m-%d')),
        data.get('date_scheduled')
    ))
    conn.commit()
    task_id = cursor.lastrowid
    task = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    conn.close()
    task_dict = dict(task)
    task_dict['completed'] = bool(task_dict['completed'])
    return jsonify(task_dict)

@app.route('/api/task', methods=['GET'])
def get_task():
    task_id = request.args.get('id')
    if not task_id:
        return jsonify({'error': 'Missing id parameter'}), 400

    conn = get_db()
    task = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    conn.close()

    if task:
        task_dict = dict(task)
        task_dict['completed'] = bool(task_dict['completed'])
        return jsonify(task_dict)
    return jsonify(None)

@app.route('/api/task', methods=['PUT'])
def update_task():
    task_id = request.args.get('id')
    if not task_id:
        return jsonify({'error': 'Missing id parameter'}), 400

    data = request.json
    conn = get_db()

    completed_at = None
    if data.get('completed'):
        completed_at = datetime.now().isoformat()

    conn.execute('''
        UPDATE tasks
        SET text = ?, completed = ?, completed_at = ?, date_scheduled = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (
        data.get('text'),
        1 if data.get('completed') else 0,
        completed_at,
        data.get('date_scheduled'),
        task_id
    ))
    conn.commit()
    task = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    conn.close()

    if task:
        task_dict = dict(task)
        task_dict['completed'] = bool(task_dict['completed'])
        return jsonify(task_dict)
    return jsonify(None)

@app.route('/api/task', methods=['PATCH'])
def patch_task():
    task_id = request.args.get('id')
    if not task_id:
        return jsonify({'error': 'Missing id parameter'}), 400

    data = request.json
    conn = get_db()

    completed_at = None
    if data.get('completed'):
        completed_at = datetime.now().isoformat()

    conn.execute('''
        UPDATE tasks
        SET completed = ?, completed_at = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (
        1 if data.get('completed') else 0,
        completed_at,
        task_id
    ))
    conn.commit()
    task = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    conn.close()

    if task:
        task_dict = dict(task)
        task_dict['completed'] = bool(task_dict['completed'])
        return jsonify(task_dict)
    return jsonify(None)

@app.route('/api/task', methods=['DELETE'])
def delete_task():
    task_id = request.args.get('id')
    if not task_id:
        return jsonify({'error': 'Missing id parameter'}), 400

    conn = get_db()
    conn.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

if __name__ == '__main__':
    init_db()
    print("\n  Outreach Tracker running at http://localhost:5050\n")
    app.run(debug=True, port=5050)
