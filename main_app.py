import os
import json
import smtplib
from email.mime.text import MIMEText

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
import google.generativeai as genai
from trello import TrelloClient

from extensions import db, bcrypt, login_manager
from models import User, Team, TrelloCredentials, TrelloCard

# --- CONFIGURATION (Loaded from Environment Variables) ---
TRELLO_API_KEY = os.environ.get("TRELLO_API_KEY")
TRELLO_API_SECRET = os.environ.get("TRELLO_API_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD") # Google App Password
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "a-default-secret-key-for-local-dev")


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = FLASK_SECRET_KEY

    # --- Database Configuration ---
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        # If the URL starts with 'postgres://', replace it with 'postgresql://'
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    else:
        # For local development
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'

    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'login'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # --- AI MODEL AND HELPER FUNCTIONS ---
    try:
        if GEMINI_API_KEY:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-1.5-flash')
            print("[*] Gemini AI model configured successfully.")
        else:
            model = None
            print("[!] GEMINI_API_KEY not found. AI features will be disabled.")
    except Exception as e:
        model = None
        print(f"[!] Error configuring Gemini AI: {e}")

    def analyze_transcript_with_ai(transcript_text):
        if not model: return {"error": "AI model is not configured or the API key is missing."}
        prompt = f"""
        Analyze the following meeting transcript. Provide your analysis ONLY in a valid JSON object format. Do not include any text, markdown formatting, or explanations before or after the JSON object.

        The JSON object must have these top-level keys: "summary", "decisions", "action_items".
        - "summary": (string) A concise, one-paragraph summary.
        - "decisions": (list of strings) A list of all concrete decisions made.
        - "action_items": (list of objects) A list of tasks. Each object must have: "task" (string), "assignee" (string), and "due_date" (string, use "Not specified" if none).

        Transcript:
        ---
        {transcript_text}
        ---

        JSON Analysis:
        """
        try:
            response = model.generate_content(prompt)
            print("\n--- Raw AI Response ---\n", response.text, "\n-----------------------\n")
            json_text = response.text.strip().replace('```json', '').replace('```', '').strip()
            if not json_text:
                return {"error": "Failed to get analysis from AI: Received an empty response. This may be due to a safety filter."}
            return json.loads(json_text)
        except json.JSONDecodeError as json_err:
            error_message = f"Failed to parse AI response as JSON. Error: {json_err}. Raw response was: '{response.text}'"
            return {"error": error_message}
        except Exception as e:
            return {"error": f"An unexpected error occurred during AI analysis: {e}"}

    def get_trello_client(user):
        if user.trello_credentials and user.trello_credentials.token:
            return TrelloClient(api_key=TRELLO_API_KEY, api_secret=TRELLO_API_SECRET, token=user.trello_credentials.token)
        return None

    def send_summary_email(recipients, analysis):
        if not SENDER_EMAIL or not SENDER_PASSWORD:
            return "Email credentials are not configured on the server."
        subject = "Meeting Summary & Action Items"
        body = f"<h2>Meeting Summary</h2><p>{analysis['summary']}</p>"
        body += "<h2>Key Decisions</h2><ul>" + "".join([f"<li>{d}</li>" for d in analysis['decisions']]) + "</ul>"
        body += "<h2>Action Items</h2><ul>" + "".join([f"<li><b>Task:</b> {i['task']} | <b>Assignee:</b> {i['assignee']} | <b>Due:</b> {i['due_date']}</li>" for i in analysis['action_items']]) + "</ul>"
        msg = MIMEText(body, 'html')
        msg['Subject'], msg['From'], msg['To'] = subject, SENDER_EMAIL, ", ".join(recipients)
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.send_message(msg)
            return "Email sent successfully."
        except Exception as e:
            return f"Failed to send email: {e}"

    def create_trello_cards(client, board_id, list_id, action_items, user_id):
        try:
            target_list = client.get_list(list_id)
            cards_created = 0
            for item in action_items:
                card_name = item['task']
                card_desc = f"Assignee: {item['assignee']}\nDue Date: {item['due_date']}"
                new_card = target_list.add_card(name=card_name, desc=card_desc)
                cards_created += 1
                db_card = TrelloCard(card_id=new_card.id, user_id=user_id, board_id=board_id, list_id=list_id, task_description=item['task'], assignee=item['assignee'], due_date_str=item['due_date'])
                db.session.add(db_card)
            db.session.commit()
            return f"{cards_created} Trello cards created successfully."
        except Exception as e:
            db.session.rollback()
            return f"Failed to create Trello cards: {e}"

    # --- ROUTES ---
    @app.route('/')
    @app.route('/home')
    @login_required
    def home():
        trello_client = get_trello_client(current_user)
        boards = trello_client.list_boards() if trello_client else []
        return render_template('index.html', trello_boards=boards)

    @app.route('/get_lists/<board_id>')
    @login_required
    def get_lists(board_id):
        trello_client = get_trello_client(current_user)
        if not trello_client: return jsonify([])
        try:
            board = trello_client.get_board(board_id)
            lists = [{"id": lst.id, "name": lst.name} for lst in board.list_lists()]
            return jsonify(lists)
        except Exception as e:
            return jsonify([])

    @app.route('/analyze', methods=['POST'])
    @login_required
    def analyze():
        transcript_text = request.form.get('transcript')
        analysis_result, notification = None, None
        if transcript_text:
            analysis_result = analyze_transcript_with_ai(transcript_text)
            if analysis_result and not analysis_result.get('error'):
                automation_messages = []
                if request.form.get('send_email') and current_user.team:
                    recipients = [member.email for member in current_user.team.members]
                    if recipients:
                        automation_messages.append(send_summary_email(recipients, analysis_result))
                if request.form.get('create_trello') and current_user.trello_credentials:
                    trello_client = get_trello_client(current_user)
                    board_id, list_id = request.form.get('trello_board_id'), request.form.get('trello_list_id')
                    if trello_client and board_id and list_id:
                        trello_status = create_trello_cards(trello_client, board_id, list_id,
                                                            analysis_result['action_items'], current_user.id)
                        automation_messages.append(trello_status)
                if automation_messages:
                    notification = {"type": "success", "message": " | ".join(automation_messages)}
        trello_client = get_trello_client(current_user)
        boards = trello_client.list_boards() if trello_client else []
        return render_template('index.html', analysis=analysis_result, transcript=transcript_text,
                               notification=notification, trello_boards=boards)

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated: return redirect(url_for('home'))
        if request.method == 'POST':
            username, email, password = request.form.get('username'), request.form.get('email'), request.form.get(
                'password')
            if User.query.filter_by(email=email).first():
                flash('Email already exists. Please log in.', 'danger')
                return redirect(url_for('login'))
            user = User(username=username, email=email, password=password)
            db.session.add(user)
            db.session.commit()
            flash('Your account has been created! You can now log in.', 'success')
            return redirect(url_for('login'))
        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated: return redirect(url_for('home'))
        if request.method == 'POST':
            email, password = request.form.get('email'), request.form.get('password')
            user = User.query.filter_by(email=email).first()
            if user and user.verify_password(password):
                login_user(user, remember=True)
                next_page = request.args.get('next')
                return redirect(next_page) if next_page else redirect(url_for('home'))
            else:
                flash('Login Unsuccessful. Please check email and password.', 'danger')
        return render_template('login.html')

    @app.route('/logout')
    def logout():
        logout_user()
        return redirect(url_for('login'))

    @app.route('/team')
    @login_required
    def team():
        return render_template('team.html')

    @app.route('/create_team', methods=['POST'])
    @login_required
    def create_team():
        team_name = request.form.get('team_name')
        if team_name:
            new_team = Team(name=team_name, owner_id=current_user.id)
            db.session.add(new_team)
            current_user.team = new_team
            db.session.commit()
            flash(f'Team "{team_name}" created successfully!', 'success')
        else:
            flash('Team name cannot be empty.', 'danger')
        return redirect(url_for('team'))

    @app.route('/invite', methods=['POST'])
    @login_required
    def invite():
        if not current_user.team:
            flash('You must create or be part of a team to invite members.', 'danger')
            return redirect(url_for('team'))
        email = request.form.get('email')
        user_to_invite = User.query.filter_by(email=email).first()
        if user_to_invite:
            user_to_invite.team = current_user.team
            db.session.commit()
            flash(f'{user_to_invite.username} has been added to your team.', 'success')
        else:
            flash('No user found with that email address.', 'danger')
        return redirect(url_for('team'))

    @app.route('/integrations')
    @login_required
    def integrations():
        return render_template('integrations.html')

    @app.route('/trello/connect')
    @login_required
    def trello_connect():
        app_name = "AI Meeting Agent"
        expiration = "never"
        scope = "read,write"
        auth_url = (f"https://trello.com/1/authorize?key={TRELLO_API_KEY}&name={app_name}"
                    f"&expiration={expiration}&response_type=token&scope={scope}")
        return render_template('connect_trello.html', auth_url=auth_url)

    @app.route('/trello/save_token', methods=['POST'])
    @login_required
    def trello_save_token():
        access_token = request.form.get('pin')
        if not access_token:
            flash('Token (PIN) is required.', 'danger')
            return redirect(url_for('trello_connect'))
        try:
            client = TrelloClient(api_key=TRELLO_API_KEY, api_secret=TRELLO_API_SECRET, token=access_token)
            trello_user = client.get_member('me')
            creds = TrelloCredentials.query.filter_by(user_id=current_user.id).first()
            if not creds:
                creds = TrelloCredentials(user_id=current_user.id)
            creds.token, creds.trello_username = access_token, trello_user.full_name
            db.session.add(creds)
            db.session.commit()
            flash('Trello account connected successfully!', 'success')
            return redirect(url_for('integrations'))
        except Exception as e:
            flash(f'Failed to connect to Trello. Please check your token. Error: {e}', 'danger')
            return redirect(url_for('trello_connect'))

    @app.route('/trello/disconnect')
    @login_required
    def trello_disconnect():
        creds = TrelloCredentials.query.filter_by(user_id=current_user.id).first()
        if creds:
            db.session.delete(creds)
            db.session.commit()
            flash('Trello account disconnected.', 'success')
        return redirect(url_for('integrations'))

    return app


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        # This will create the sqlite file locally if it doesn't exist
        if not os.environ.get('DATABASE_URL'):
            db.create_all()
    app.run(debug=True)