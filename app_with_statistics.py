from flask import Flask, render_template, request, jsonify
import sqlite3
import json
import os

app = Flask(__name__)
DB_PATH = 'stretching_coach.db'

# Database initialization
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Create tables
    c.execute('''CREATE TABLE IF NOT EXISTS studios
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  payment_per_client REAL NOT NULL,
                  minimum_payment REAL NOT NULL DEFAULT 0,
                  start_count_from INTEGER NOT NULL DEFAULT 1,
                  payment_individual REAL NOT NULL DEFAULT 0,
                  color TEXT DEFAULT '#FF6B6B')''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS training_sessions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  studio_id INTEGER NOT NULL,
                  date TEXT NOT NULL,
                  time TEXT NOT NULL,
                  duration INTEGER NOT NULL,
                  capacity INTEGER NOT NULL,
                  coach_name TEXT NOT NULL,
                  session_type TEXT NOT NULL,
                  paid INTEGER DEFAULT 0,
                  attendees TEXT DEFAULT '[]',
                  FOREIGN KEY (studio_id) REFERENCES studios(id))''')
    
    # Database is ready - no sample data inserted
    # Start with empty tables for fresh installation
    
    # Check if we need to migrate existing studios table
    try:
        c.execute("SELECT minimum_payment FROM studios LIMIT 1")
    except sqlite3.OperationalError:
        # Column doesn't exist, need to migrate
        print("Migrating database: adding new columns to studios table...")
        try:
            c.execute("ALTER TABLE studios ADD COLUMN minimum_payment REAL NOT NULL DEFAULT 0")
            c.execute("ALTER TABLE studios ADD COLUMN start_count_from INTEGER NOT NULL DEFAULT 1")
            print("Migration complete!")
        except sqlite3.OperationalError as e:
            print(f"Migration error (might be okay if columns already exist): {e}")
    
    # Check if we need to add payment_individual column
    try:
        c.execute("SELECT payment_individual FROM studios LIMIT 1")
    except sqlite3.OperationalError:
        # Column doesn't exist, need to add it
        print("Migrating database: adding payment_individual column...")
        try:
            c.execute("ALTER TABLE studios ADD COLUMN payment_individual REAL NOT NULL DEFAULT 0")
            print("Migration complete!")
        except sqlite3.OperationalError as e:
            print(f"Migration error (might be okay if column already exists): {e}")
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# Helper functions
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def calculate_payment(session_dict, studio_dict):
    if not studio_dict:
        return 0
    
    # Check if it's individual training
    if session_dict['session_type'] == 'Individual':
        return studio_dict['payment_individual']
    
    # For group training
    attendees = json.loads(session_dict['attendees']) if session_dict['attendees'] else []
    attendee_count = len(attendees)
    
    # New algorithm:
    # If clients <= threshold: use minimum payment
    # If clients > threshold: multiply clients by per-client rate
    if attendee_count <= studio_dict['start_count_from']:
        return studio_dict['minimum_payment']
    else:
        return attendee_count * studio_dict['payment_per_client']


# Routes
@app.route('/')
def index():
    return render_template('index.html')

# Studio API
@app.route('/api/studios', methods=['GET'])
def get_studios():
    conn = get_db()
    studios = conn.execute('SELECT * FROM studios').fetchall()
    conn.close()
    return jsonify([{
        'id': s['id'],
        'name': s['name'],
        'paymentPerClient': s['payment_per_client'],
        'minimumPayment': s['minimum_payment'],
        'startCountFrom': s['start_count_from'],
        'paymentIndividual': s['payment_individual'],
        'color': s['color']
    } for s in studios])

@app.route('/api/studios', methods=['POST'])
def add_studio():
    data = request.json
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO studios (name, payment_per_client, minimum_payment, start_count_from, payment_individual, color) 
                 VALUES (?, ?, ?, ?, ?, ?)''',
              (data['name'], data['paymentPerClient'], data['minimumPayment'], 
               data['startCountFrom'], data['paymentIndividual'], data.get('color', '#FF6B6B')))
    studio_id = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({
        'id': studio_id,
        'name': data['name'],
        'paymentPerClient': data['paymentPerClient'],
        'minimumPayment': data['minimumPayment'],
        'startCountFrom': data['startCountFrom'],
        'paymentIndividual': data['paymentIndividual'],
        'color': data.get('color', '#FF6B6B')
    })

@app.route('/api/studios/<int:studio_id>', methods=['DELETE'])
def delete_studio(studio_id):
    conn = get_db()
    c = conn.cursor()
    
    # Check if studio exists
    studio = conn.execute('SELECT * FROM studios WHERE id = ?', (studio_id,)).fetchone()
    if not studio:
        conn.close()
        return jsonify({'error': 'Studio not found'}), 404
    
    # Check if studio has any sessions
    sessions = conn.execute('SELECT COUNT(*) FROM training_sessions WHERE studio_id = ?', (studio_id,)).fetchone()[0]
    if sessions > 0:
        conn.close()
        return jsonify({'error': f'Cannot delete studio. It has {sessions} training session(s) associated with it.'}), 400
    
    # Delete the studio
    c.execute('DELETE FROM studios WHERE id = ?', (studio_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Studio deleted successfully'})

@app.route('/api/studios/<int:studio_id>', methods=['PUT'])
def update_studio(studio_id):
    data = request.json
    conn = get_db()
    c = conn.cursor()
    
    # Check if studio exists
    studio = conn.execute('SELECT * FROM studios WHERE id = ?', (studio_id,)).fetchone()
    if not studio:
        conn.close()
        return jsonify({'error': 'Studio not found'}), 404
    
    # Update the studio
    c.execute('''UPDATE studios 
                 SET name = ?, payment_per_client = ?, minimum_payment = ?, start_count_from = ?, payment_individual = ?, color = ?
                 WHERE id = ?''',
              (data['name'], data['paymentPerClient'], data['minimumPayment'], 
               data['startCountFrom'], data['paymentIndividual'], data['color'], studio_id))
    conn.commit()
    conn.close()
    
    return jsonify({
        'id': studio_id,
        'name': data['name'],
        'paymentPerClient': data['paymentPerClient'],
        'minimumPayment': data['minimumPayment'],
        'startCountFrom': data['startCountFrom'],
        'paymentIndividual': data['paymentIndividual'],
        'color': data['color']
    })

# Training Session API
@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    conn = get_db()
    sessions = conn.execute('SELECT * FROM training_sessions').fetchall()
    studios = {s['id']: s for s in conn.execute('SELECT * FROM studios').fetchall()}
    
    result = []
    for s in sessions:
        studio = studios.get(s['studio_id'])
        result.append({
            'id': s['id'],
            'studioId': s['studio_id'],
            'date': s['date'],
            'time': s['time'],
            'duration': s['duration'],
            'capacity': s['capacity'],
            'coachName': s['coach_name'],
            'sessionType': s['session_type'],
            'paid': bool(s['paid']),
            'attendees': json.loads(s['attendees']) if s['attendees'] else [],
            'payment': calculate_payment(s, studio)
        })
    
    conn.close()
    return jsonify(result)

@app.route('/api/sessions', methods=['POST'])
def add_session():
    data = request.json
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO training_sessions 
                 (studio_id, date, time, duration, capacity, coach_name, session_type, paid, attendees)
                 VALUES (?, ?, ?, ?, ?, ?, ?, 0, '[]')''',
              (data['studioId'], data['date'], data['time'], data['duration'],
               data['capacity'], data['coachName'], data['sessionType']))
    session_id = c.lastrowid
    conn.commit()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ?', (session_id,)).fetchone()
    studio = conn.execute('SELECT * FROM studios WHERE id = ?', (session['studio_id'],)).fetchone()
    conn.close()
    
    return jsonify({
        'id': session['id'],
        'studioId': session['studio_id'],
        'date': session['date'],
        'time': session['time'],
        'duration': session['duration'],
        'capacity': session['capacity'],
        'coachName': session['coach_name'],
        'sessionType': session['session_type'],
        'paid': bool(session['paid']),
        'attendees': json.loads(session['attendees']) if session['attendees'] else [],
        'payment': calculate_payment(session, studio)
    })

@app.route('/api/sessions/<int:session_id>/attendees', methods=['POST'])
def add_attendee(session_id):
    data = request.json
    conn = get_db()
    c = conn.cursor()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ?', (session_id,)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404
    
    attendees = json.loads(session['attendees']) if session['attendees'] else []
    if data['name'] not in attendees and len(attendees) < session['capacity']:
        attendees.append(data['name'])
        c.execute('UPDATE training_sessions SET attendees = ? WHERE id = ?',
                 (json.dumps(attendees), session_id))
        conn.commit()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ?', (session_id,)).fetchone()
    studio = conn.execute('SELECT * FROM studios WHERE id = ?', (session['studio_id'],)).fetchone()
    conn.close()
    
    return jsonify({
        'attendees': json.loads(session['attendees']) if session['attendees'] else [],
        'payment': calculate_payment(session, studio)
    })

@app.route('/api/sessions/<int:session_id>/attendees/<attendee_name>', methods=['DELETE'])
def remove_attendee(session_id, attendee_name):
    conn = get_db()
    c = conn.cursor()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ?', (session_id,)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404
    
    attendees = json.loads(session['attendees']) if session['attendees'] else []
    if attendee_name in attendees:
        attendees.remove(attendee_name)
        c.execute('UPDATE training_sessions SET attendees = ? WHERE id = ?',
                 (json.dumps(attendees), session_id))
        conn.commit()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ?', (session_id,)).fetchone()
    studio = conn.execute('SELECT * FROM studios WHERE id = ?', (session['studio_id'],)).fetchone()
    conn.close()
    
    return jsonify({
        'attendees': json.loads(session['attendees']) if session['attendees'] else [],
        'payment': calculate_payment(session, studio)
    })

@app.route('/api/sessions/<int:session_id>/mark-paid', methods=['PUT'])
def mark_session_paid(session_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE training_sessions SET paid = 1 WHERE id = ?', (session_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/sessions/<int:session_id>', methods=['DELETE'])
def delete_session(session_id):
    conn = get_db()
    c = conn.cursor()
    
    # Check if session exists
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ?', (session_id,)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404
    
    # Delete the session
    c.execute('DELETE FROM training_sessions WHERE id = ?', (session_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Session deleted successfully'})

@app.route('/api/sessions/<int:session_id>', methods=['PUT'])
def update_session(session_id):
    data = request.json
    conn = get_db()
    c = conn.cursor()
    
    # Check if session exists
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ?', (session_id,)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404
    
    # Update the session
    c.execute('''UPDATE training_sessions 
                 SET studio_id = ?, date = ?, time = ?, session_type = ?
                 WHERE id = ?''',
              (data['studioId'], data['date'], data['time'], data['sessionType'], session_id))
    conn.commit()
    
    # Get updated session with payment calculation
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ?', (session_id,)).fetchone()
    studio = conn.execute('SELECT * FROM studios WHERE id = ?', (session['studio_id'],)).fetchone()
    conn.close()
    
    return jsonify({
        'id': session['id'],
        'studioId': session['studio_id'],
        'date': session['date'],
        'time': session['time'],
        'duration': session['duration'],
        'capacity': session['capacity'],
        'coachName': session['coach_name'],
        'sessionType': session['session_type'],
        'paid': bool(session['paid']),
        'attendees': json.loads(session['attendees']) if session['attendees'] else [],
        'payment': calculate_payment(session, studio)
    })

@app.route('/api/stats', methods=['GET'])
def get_stats():
    conn = get_db()
    sessions = conn.execute('SELECT * FROM training_sessions').fetchall()
    studios = {s['id']: s for s in conn.execute('SELECT * FROM studios').fetchall()}
    
    total_sessions = len(sessions)
    total_attendees = sum(len(json.loads(s['attendees']) if s['attendees'] else []) for s in sessions)
    paid_revenue = sum(calculate_payment(s, studios.get(s['studio_id'])) for s in sessions if s['paid'])
    pending_revenue = sum(calculate_payment(s, studios.get(s['studio_id'])) for s in sessions if not s['paid'])
    
    conn.close()
    
    return jsonify({
        'totalSessions': total_sessions,
        'totalAttendees': total_attendees,
        'paidRevenue': paid_revenue,
        'pendingRevenue': pending_revenue
    })

@app.route('/api/stats/filtered', methods=['GET'])
def get_filtered_stats():
    studio_id = request.args.get('studioId', type=int)
    date_from = request.args.get('dateFrom')
    date_to = request.args.get('dateTo')
    
    conn = get_db()
    
    # Build query with filters
    query = 'SELECT * FROM training_sessions WHERE 1=1'
    params = []
    
    if studio_id:
        query += ' AND studio_id = ?'
        params.append(studio_id)
    
    if date_from:
        query += ' AND date >= ?'
        params.append(date_from)
    
    if date_to:
        query += ' AND date <= ?'
        params.append(date_to)
    
    sessions = conn.execute(query, params).fetchall()
    studios = {s['id']: s for s in conn.execute('SELECT * FROM studios').fetchall()}
    
    # Calculate statistics
    total_sessions = len(sessions)
    total_attendees = sum(len(json.loads(s['attendees']) if s['attendees'] else []) for s in sessions)
    paid_revenue = sum(calculate_payment(s, studios.get(s['studio_id'])) for s in sessions if s['paid'])
    pending_revenue = sum(calculate_payment(s, studios.get(s['studio_id'])) for s in sessions if not s['paid'])
    
    # Count by type
    group_sessions = sum(1 for s in sessions if s['session_type'] == 'Group')
    individual_sessions = sum(1 for s in sessions if s['session_type'] == 'Individual')
    
    # Get detailed sessions for the list
    detailed_sessions = []
    for s in sessions:
        studio = studios.get(s['studio_id'])
        detailed_sessions.append({
            'id': s['id'],
            'studioId': s['studio_id'],
            'studioName': studio['name'] if studio else 'Unknown',
            'date': s['date'],
            'time': s['time'],
            'sessionType': s['session_type'],
            'paid': bool(s['paid']),
            'attendees': json.loads(s['attendees']) if s['attendees'] else [],
            'payment': calculate_payment(s, studio)
        })
    
    # Sort by date (most recent first)
    detailed_sessions.sort(key=lambda x: x['date'] + ' ' + x['time'], reverse=True)
    
    conn.close()
    
    return jsonify({
        'totalSessions': total_sessions,
        'totalAttendees': total_attendees,
        'paidRevenue': paid_revenue,
        'pendingRevenue': pending_revenue,
        'groupSessions': group_sessions,
        'individualSessions': individual_sessions,
        'sessions': detailed_sessions
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
