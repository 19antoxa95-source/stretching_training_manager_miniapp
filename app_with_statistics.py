from flask import Flask, render_template, request, jsonify
import sqlite3
import json
import os
import hashlib

app = Flask(__name__)
DB_PATH = 'stretching_coach.db'

# Helper function to get user_id from request
def get_user_id():
    """
    Get user_id from Telegram WebApp init data or create anonymous user
    """
    # Try to get from Telegram WebApp init data
    telegram_data = request.headers.get('X-Telegram-User-Id')
    if telegram_data:
        return f"tg_{telegram_data}"
    
    # Try to get from custom header (set by frontend)
    user_id = request.headers.get('X-User-Id')
    if user_id:
        return user_id
    
    # For web users without Telegram: use session-based ID or IP-based
    # You can also use Flask sessions here
    ip = request.remote_addr
    session_id = request.cookies.get('session_id')
    
    if session_id:
        return f"web_{session_id}"
    else:
        # Create hash from IP (not perfect but works for demo)
        return f"web_{hashlib.md5(ip.encode()).hexdigest()[:8]}"

# Database initialization
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Create tables with user_id for multi-user support
    c.execute('''CREATE TABLE IF NOT EXISTS studios
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id TEXT NOT NULL,
                  name TEXT NOT NULL,
                  payment_per_client REAL NOT NULL,
                  minimum_payment REAL NOT NULL DEFAULT 0,
                  start_count_from INTEGER NOT NULL DEFAULT 1,
                  payment_individual REAL NOT NULL DEFAULT 0,
                  color TEXT DEFAULT '#FF6B6B')''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS training_sessions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id TEXT NOT NULL,
                  studio_id INTEGER NOT NULL,
                  date TEXT NOT NULL,
                  time TEXT NOT NULL,
                  duration INTEGER NOT NULL,
                  capacity INTEGER NOT NULL,
                  coach_name TEXT NOT NULL,
                  session_type TEXT NOT NULL,
                  paid INTEGER DEFAULT 0,
                  attendees TEXT DEFAULT '[]',
                  payment_amount REAL DEFAULT 0,
                  FOREIGN KEY (studio_id) REFERENCES studios(id))''')
    
    # Create indexes for faster queries
    c.execute('CREATE INDEX IF NOT EXISTS idx_studios_user ON studios(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_sessions_user ON training_sessions(user_id)')
    
    # Migration: Add user_id to existing tables if not present
    try:
        c.execute("SELECT user_id FROM studios LIMIT 1")
    except sqlite3.OperationalError:
        # Column doesn't exist, need to migrate
        print("Migrating database: adding user_id to studios...")
        c.execute("ALTER TABLE studios ADD COLUMN user_id TEXT DEFAULT 'legacy_user'")
        c.execute("UPDATE studios SET user_id = 'legacy_user' WHERE user_id IS NULL")
        print("Migration complete for studios!")
    
    try:
        c.execute("SELECT user_id FROM training_sessions LIMIT 1")
    except sqlite3.OperationalError:
        # Column doesn't exist, need to migrate
        print("Migrating database: adding user_id to training_sessions...")
        c.execute("ALTER TABLE training_sessions ADD COLUMN user_id TEXT DEFAULT 'legacy_user'")
        c.execute("UPDATE training_sessions SET user_id = 'legacy_user' WHERE user_id IS NULL")
        print("Migration complete for training_sessions!")
    
    # Migration: Add payment_individual to existing studios if not present
    try:
        c.execute("SELECT payment_individual FROM studios LIMIT 1")
    except sqlite3.OperationalError:
        # Column doesn't exist, need to migrate
        print("Migrating database: adding payment_individual to studios...")
        c.execute("ALTER TABLE studios ADD COLUMN payment_individual REAL DEFAULT 0")
        print("Migration complete for payment_individual!")
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# Helper functions
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def calculate_payment(attendee_count, studio_dict):
    if not studio_dict:
        return 0
    
    # Start with minimum payment
    total = studio_dict['minimum_payment']
    
    # Add per-client payment if attendees exceed the start_count_from threshold
    if attendee_count > studio_dict['start_count_from']:
        additional_clients = attendee_count - studio_dict['start_count_from']
        total += additional_clients * studio_dict['payment_per_client']
    
    return total

# Routes
@app.route('/')
def index():
    return render_template('index.html')

# Studio API - All filtered by user_id
@app.route('/api/studios', methods=['GET'])
def get_studios():
    user_id = get_user_id()
    conn = get_db()
    studios = conn.execute('SELECT * FROM studios WHERE user_id = ?', (user_id,)).fetchall()
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
    user_id = get_user_id()
    data = request.json
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO studios (user_id, name, payment_per_client, minimum_payment, start_count_from, payment_individual, color) 
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (user_id, data['name'], data['paymentPerClient'], data['minimumPayment'], 
               data['startCountFrom'], data.get('paymentIndividual', 0), data.get('color', '#FF6B6B')))
    studio_id = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({
        'id': studio_id,
        'name': data['name'],
        'paymentPerClient': data['paymentPerClient'],
        'minimumPayment': data['minimumPayment'],
        'startCountFrom': data['startCountFrom'],
        'paymentIndividual': data.get('paymentIndividual', 0),
        'color': data.get('color', '#FF6B6B')
    })

@app.route('/api/studios/<int:studio_id>', methods=['DELETE'])
def delete_studio(studio_id):
    user_id = get_user_id()
    conn = get_db()
    c = conn.cursor()
    
    # Check if studio exists AND belongs to this user
    studio = conn.execute('SELECT * FROM studios WHERE id = ? AND user_id = ?', (studio_id, user_id)).fetchone()
    if not studio:
        conn.close()
        return jsonify({'error': 'Studio not found'}), 404
    
    # Check if studio has any sessions
    sessions = conn.execute('SELECT COUNT(*) FROM training_sessions WHERE studio_id = ? AND user_id = ?', 
                          (studio_id, user_id)).fetchone()[0]
    if sessions > 0:
        conn.close()
        return jsonify({'error': f'Cannot delete studio. It has {sessions} training session(s) associated with it.'}), 400
    
    # Delete the studio
    c.execute('DELETE FROM studios WHERE id = ? AND user_id = ?', (studio_id, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Studio deleted successfully'})

@app.route('/api/studios/<int:studio_id>', methods=['PUT'])
def update_studio(studio_id):
    user_id = get_user_id()
    data = request.json
    conn = get_db()
    c = conn.cursor()
    
    # Check if studio exists AND belongs to this user
    studio = conn.execute('SELECT * FROM studios WHERE id = ? AND user_id = ?', (studio_id, user_id)).fetchone()
    if not studio:
        conn.close()
        return jsonify({'error': 'Studio not found'}), 404
    
    # Update the studio
    c.execute('''UPDATE studios 
                 SET name = ?, payment_per_client = ?, minimum_payment = ?, start_count_from = ?, payment_individual = ?, color = ?
                 WHERE id = ? AND user_id = ?''',
              (data['name'], data['paymentPerClient'], data['minimumPayment'], 
               data['startCountFrom'], data.get('paymentIndividual', 0), data['color'], studio_id, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({
        'id': studio_id,
        'name': data['name'],
        'paymentPerClient': data['paymentPerClient'],
        'minimumPayment': data['minimumPayment'],
        'startCountFrom': data['startCountFrom'],
        'paymentIndividual': data.get('paymentIndividual', 0),
        'color': data['color']
    })

# Training Session API - All filtered by user_id
@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    user_id = get_user_id()
    conn = get_db()
    sessions = conn.execute('SELECT * FROM training_sessions WHERE user_id = ?', (user_id,)).fetchall()
    studios = {s['id']: s for s in conn.execute('SELECT * FROM studios WHERE user_id = ?', (user_id,)).fetchall()}
    
    result = []
    for s in sessions:
        studio = studios.get(s['studio_id'])
        attendees = json.loads(s['attendees']) if s['attendees'] else []
        payment = calculate_payment(len(attendees), studio) if studio else 0
        
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
            'attendees': attendees,
            'payment': payment
        })
    
    conn.close()
    return jsonify(result)

@app.route('/api/sessions', methods=['POST'])
def add_session():
    user_id = get_user_id()
    data = request.json
    conn = get_db()
    c = conn.cursor()
    
    # Verify studio belongs to user
    studio = conn.execute('SELECT * FROM studios WHERE id = ? AND user_id = ?', 
                         (data['studioId'], user_id)).fetchone()
    if not studio:
        conn.close()
        return jsonify({'error': 'Studio not found'}), 404
    
    c.execute('''INSERT INTO training_sessions 
                 (user_id, studio_id, date, time, duration, capacity, coach_name, session_type, paid, attendees)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, '[]')''',
              (user_id, data['studioId'], data['date'], data['time'], data['duration'],
               data['capacity'], data['coachName'], data['sessionType']))
    session_id = c.lastrowid
    conn.commit()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ? AND user_id = ?', 
                          (session_id, user_id)).fetchone()
    conn.close()
    
    payment = calculate_payment(0, studio)
    
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
        'attendees': [],
        'payment': payment
    })

@app.route('/api/sessions/<int:session_id>', methods=['DELETE'])
def delete_session(session_id):
    user_id = get_user_id()
    conn = get_db()
    c = conn.cursor()
    
    # Check if session exists AND belongs to this user
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ? AND user_id = ?', 
                          (session_id, user_id)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404
    
    # Delete the session
    c.execute('DELETE FROM training_sessions WHERE id = ? AND user_id = ?', (session_id, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Session deleted successfully'})

@app.route('/api/sessions/<int:session_id>', methods=['PUT'])
def update_session(session_id):
    user_id = get_user_id()
    data = request.json
    conn = get_db()
    c = conn.cursor()
    
    # Check if session exists AND belongs to this user
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ? AND user_id = ?', 
                          (session_id, user_id)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404
    
    # Update the session
    c.execute('''UPDATE training_sessions 
                 SET studio_id = ?, date = ?, time = ?, duration = ?, 
                     capacity = ?, coach_name = ?, session_type = ?
                 WHERE id = ? AND user_id = ?''',
              (data['studioId'], data['date'], data['time'], data['duration'],
               data['capacity'], data['coachName'], data['sessionType'], 
               session_id, user_id))
    conn.commit()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ? AND user_id = ?', 
                          (session_id, user_id)).fetchone()
    studio = conn.execute('SELECT * FROM studios WHERE id = ? AND user_id = ?', 
                         (session['studio_id'], user_id)).fetchone()
    conn.close()
    
    attendees = json.loads(session['attendees']) if session['attendees'] else []
    payment = calculate_payment(len(attendees), studio) if studio else 0
    
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
        'attendees': attendees,
        'payment': payment
    })

@app.route('/api/sessions/<int:session_id>/attendees', methods=['POST'])
def add_attendee(session_id):
    user_id = get_user_id()
    data = request.json
    conn = get_db()
    c = conn.cursor()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ? AND user_id = ?', 
                          (session_id, user_id)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404
    
    attendees = json.loads(session['attendees']) if session['attendees'] else []
    if data['name'] not in attendees and len(attendees) < session['capacity']:
        attendees.append(data['name'])
        c.execute('UPDATE training_sessions SET attendees = ? WHERE id = ? AND user_id = ?',
                 (json.dumps(attendees), session_id, user_id))
        conn.commit()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ? AND user_id = ?', 
                          (session_id, user_id)).fetchone()
    studio = conn.execute('SELECT * FROM studios WHERE id = ? AND user_id = ?', 
                         (session['studio_id'], user_id)).fetchone()
    conn.close()
    
    attendees = json.loads(session['attendees']) if session['attendees'] else []
    payment = calculate_payment(len(attendees), studio) if studio else 0
    
    return jsonify({
        'attendees': attendees,
        'payment': payment
    })

@app.route('/api/sessions/<int:session_id>/attendees/<attendee_name>', methods=['DELETE'])
def remove_attendee(session_id, attendee_name):
    user_id = get_user_id()
    conn = get_db()
    c = conn.cursor()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ? AND user_id = ?', 
                          (session_id, user_id)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404
    
    attendees = json.loads(session['attendees']) if session['attendees'] else []
    if attendee_name in attendees:
        attendees.remove(attendee_name)
        c.execute('UPDATE training_sessions SET attendees = ? WHERE id = ? AND user_id = ?',
                 (json.dumps(attendees), session_id, user_id))
        conn.commit()
    
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ? AND user_id = ?', 
                          (session_id, user_id)).fetchone()
    studio = conn.execute('SELECT * FROM studios WHERE id = ? AND user_id = ?', 
                         (session['studio_id'], user_id)).fetchone()
    conn.close()
    
    attendees = json.loads(session['attendees']) if session['attendees'] else []
    payment = calculate_payment(len(attendees), studio) if studio else 0
    
    return jsonify({
        'attendees': attendees,
        'payment': payment
    })

@app.route('/api/sessions/<int:session_id>/mark-paid', methods=['PUT'])
def mark_session_paid(session_id):
    user_id = get_user_id()
    conn = get_db()
    c = conn.cursor()
    
    # Check if session exists AND belongs to this user
    session = conn.execute('SELECT * FROM training_sessions WHERE id = ? AND user_id = ?', 
                          (session_id, user_id)).fetchone()
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404
    
    c.execute('UPDATE training_sessions SET paid = 1 WHERE id = ? AND user_id = ?', 
             (session_id, user_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    user_id = get_user_id()
    conn = get_db()
    sessions = conn.execute('SELECT * FROM training_sessions WHERE user_id = ?', (user_id,)).fetchall()
    studios = {s['id']: s for s in conn.execute('SELECT * FROM studios WHERE user_id = ?', (user_id,)).fetchall()}
    
    total_sessions = len(sessions)
    total_attendees = 0
    paid_revenue = 0
    pending_revenue = 0
    
    for s in sessions:
        studio = studios.get(s['studio_id'])
        attendees = json.loads(s['attendees']) if s['attendees'] else []
        total_attendees += len(attendees)
        payment = calculate_payment(len(attendees), studio) if studio else 0
        
        if s['paid']:
            paid_revenue += payment
        else:
            pending_revenue += payment
    
    conn.close()
    
    return jsonify({
        'totalSessions': total_sessions,
        'totalAttendees': total_attendees,
        'paidRevenue': paid_revenue,
        'pendingRevenue': pending_revenue
    })

@app.route('/api/stats/filtered', methods=['GET'])
def get_filtered_stats():
    user_id = get_user_id()
    studio_id = request.args.get('studioId')
    date_from = request.args.get('dateFrom')
    date_to = request.args.get('dateTo')
    
    conn = get_db()
    
    # Build query with filters
    query = 'SELECT * FROM training_sessions WHERE user_id = ?'
    params = [user_id]
    
    if studio_id and studio_id != 'all':
        query += ' AND studio_id = ?'
        params.append(int(studio_id))
    
    if date_from:
        query += ' AND date >= ?'
        params.append(date_from)
    
    if date_to:
        query += ' AND date <= ?'
        params.append(date_to)
    
    sessions = conn.execute(query, params).fetchall()
    studios = {s['id']: s for s in conn.execute('SELECT * FROM studios WHERE user_id = ?', (user_id,)).fetchall()}
    
    total_sessions = len(sessions)
    total_attendees = 0
    paid_revenue = 0
    pending_revenue = 0
    group_sessions = 0
    individual_sessions = 0
    detailed_sessions = []
    
    for s in sessions:
        studio = studios.get(s['studio_id'])
        attendees = json.loads(s['attendees']) if s['attendees'] else []
        attendee_count = len(attendees)
        total_attendees += attendee_count
        payment = calculate_payment(attendee_count, studio) if studio else 0
        
        if s['session_type'].lower() == 'group':
            group_sessions += 1
        else:
            individual_sessions += 1
        
        if s['paid']:
            paid_revenue += payment
        else:
            pending_revenue += payment
        
        detailed_sessions.append({
            'id': s['id'],
            'date': s['date'],
            'time': s['time'],
            'studioName': studio['name'] if studio else 'Unknown',
            'coachName': s['coach_name'],
            'sessionType': s['session_type'],
            'attendees': attendee_count,
            'capacity': s['capacity'],
            'payment': payment,
            'paid': bool(s['paid'])
        })
    
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

# User info endpoint (for testing)
@app.route('/api/user-info', methods=['GET'])
def get_user_info():
    user_id = get_user_id()
    return jsonify({
        'userId': user_id,
        'userType': 'telegram' if user_id.startswith('tg_') else 'web'
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=port)
