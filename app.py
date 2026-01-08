from flask import Flask, render_template, request, jsonify
import sqlite3
from datetime import datetime
import os

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), 'outreach.db')

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
    conn = get_db()
    prospects = conn.execute('SELECT * FROM prospects ORDER BY updated_at DESC').fetchall()
    conn.close()
    return jsonify([dict(p) for p in prospects])

@app.route('/api/prospects', methods=['POST'])
def add_prospect():
    data = request.json
    conn = get_db()
    cursor = conn.execute('''
        INSERT INTO prospects (name, company, email, linkedin, notes, status, next_followup)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('name'),
        data.get('company'),
        data.get('email'),
        data.get('linkedin'),
        data.get('notes'),
        data.get('status', 'new'),
        data.get('next_followup')
    ))
    conn.commit()
    prospect_id = cursor.lastrowid
    prospect = conn.execute('SELECT * FROM prospects WHERE id = ?', (prospect_id,)).fetchone()
    conn.close()
    return jsonify(dict(prospect))

@app.route('/api/prospects/<int:id>', methods=['PUT'])
def update_prospect(id):
    data = request.json
    conn = get_db()
    conn.execute('''
        UPDATE prospects
        SET name = ?, company = ?, email = ?, linkedin = ?, notes = ?,
            status = ?, next_followup = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (
        data.get('name'),
        data.get('company'),
        data.get('email'),
        data.get('linkedin'),
        data.get('notes'),
        data.get('status'),
        data.get('next_followup'),
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
    conn = get_db()
    conn.execute('''
        UPDATE prospects SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
    ''', (data.get('status'), id))
    conn.commit()
    prospect = conn.execute('SELECT * FROM prospects WHERE id = ?', (id,)).fetchone()
    conn.close()
    return jsonify(dict(prospect))

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
              status_counts.get('closed', 0) + status_counts.get('lost', 0)) /
             max(total - status_counts.get('new', 0), 1) * 100), 1
        ) if total > status_counts.get('new', 0) else 0
    })

if __name__ == '__main__':
    init_db()
    print("\n  Outreach Tracker running at http://localhost:5050\n")
    app.run(debug=True, port=5050)
