import os
import json
import smtplib
from email.mime.text import MIMEText
import requests
from jira import JIRA  # Make sure this is imported
from jira.exceptions import JIRAError  # Import specific Jira errors

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
import google.generativeai as genai
from trello import TrelloClient

from extensions import db, bcrypt, login_manager
from models import User, Team, TrelloCredentials, TrelloCard, JiraCredentials
from dotenv import load_dotenv

load_dotenv()
# --- CONFIGURATION (Unchanged) ---
TRELLO_API_KEY = os.environ.get("TRELLO_API_KEY")
TRELLO_API_SECRET = os.environ.get("TRELLO_API_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "a-default-secret-key-for-local-dev")


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = FLASK_SECRET_KEY

    # --- Database Configuration (Unchanged) ---
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        if database_url.startswith("postgres://"): database_url = database_url.replace("postgres://", "postgresql://",
                                                                                       1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    else:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'

    db.init_app(app);
    bcrypt.init_app(app);
    login_manager.init_app(app)
    login_manager.login_view = 'login'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # --- AI MODEL AND HELPER FUNCTIONS (Unchanged) ---
    try:
        if GEMINI_API_KEY:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-2.5-flash')
            print("[*] Gemini AI configured.")
        else:
            model = None; print("[!] GEMINI_API_KEY missing.")
    except Exception as e:
        model = None; print(f"[!] Gemini AI Error: {e}")

    def analyze_transcript_with_ai(transcript_text):
        if not model: return {"error": "AI model not configured."}
        # --- PROMPT RESTORED ---
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
            response = model.generate_content(prompt);
            print(f"Raw AI: {response.text}")
            json_text = response.text.strip().replace('```json', '').replace('```', '').strip()
            if not json_text: return {"error": "AI empty response."}
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            return {"error": f"AI JSON Parse Error: {e}. Raw: '{response.text}'"}
        except Exception as e:
            return {"error": f"AI Error: {e}"}

    def get_trello_client(user):
        if user.trello_credentials: return TrelloClient(api_key=TRELLO_API_KEY, api_secret=TRELLO_API_SECRET,
                                                        token=user.trello_credentials.token)
        return None

    def send_summary_email(recipients, analysis):
        if not SENDER_EMAIL or not SENDER_PASSWORD: return "Email creds not configured."
        subject = "Meeting Summary & Action Items"
        body = f"<h2>Summary</h2><p>{analysis.get('summary', 'N/A')}</p>"
        body += "<h2>Decisions</h2><ul>" + "".join([f"<li>{d}</li>" for d in analysis.get('decisions', [])]) + "</ul>"
        body += "<h2>Action Items</h2><ul>" + "".join([
                                                          f"<li><b>Task:</b> {i.get('task', 'N/A')} | <b>Assignee:</b> {i.get('assignee', 'N/A')} | <b>Due:</b> {i.get('due_date', 'N/A')}</li>"
                                                          for i in analysis.get('action_items', [])]) + "</ul>"
        msg = MIMEText(body, 'html');
        msg['Subject'], msg['From'], msg['To'] = subject, SENDER_EMAIL, ", ".join(recipients)
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD); server.send_message(msg)
            return "Email sent successfully."
        except Exception as e:
            return f"Failed to send email: {e}"

    def create_trello_cards(client, board_id, list_id, action_items, user_id):
        try:
            target_list = client.get_list(list_id);
            cards_created = 0
            for item in action_items:
                card_name = item.get('task', 'Untitled Task')
                card_desc = f"Assignee: {item.get('assignee', 'N/A')}\nDue Date: {item.get('due_date', 'N/A')}"
                new_card = target_list.add_card(name=card_name, desc=card_desc);
                cards_created += 1
                db_card = TrelloCard(card_id=new_card.id, user_id=user_id, board_id=board_id, list_id=list_id,
                                     task_description=item.get('task', 'No desc'), assignee=item.get('assignee'),
                                     due_date_str=item.get('due_date'))
                db.session.add(db_card)
            db.session.commit();
            return f"{cards_created} Trello cards created."
        except Exception as e:
            db.session.rollback(); return f"Failed Trello cards: {e}"

    def send_to_slack(team, analysis):
        if not team or not team.slack_webhook_url:
            return "Slack is not configured for this team."
        webhook_url = team.slack_webhook_url

        # --- SLACK BLOCKS RESTORED ---
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "📝 Meeting Summary",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": analysis.get('summary', 'No summary available.')
                }
            },
            {
                "type": "divider"
            }
        ]
        # Add Decisions if any
        decisions = analysis.get('decisions')
        if decisions:
            blocks.extend([
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*⚖️ Key Decisions:*\n" + "\n".join([f"• {d}" for d in decisions])
                    }
                },
                {"type": "divider"}
            ])
        # Add Action Items if any
        action_items = analysis.get('action_items')
        if action_items:
            action_items_text = "*✅ Action Items:*\n"
            for item in action_items:
                action_items_text += f"• *Task:* {item.get('task', 'N/A')} | *Assignee:* {item.get('assignee', 'N/A')} | *Due:* {item.get('due_date', 'N/A')}\n"
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": action_items_text
                }
            })
        # -----------------------------

        payload = {"blocks": blocks}
        try:
            response = requests.post(webhook_url, json=payload, timeout=10);
            response.raise_for_status()
            if response.text == 'ok':
                print("[*] Slack msg sent."); return "Summary sent to Slack."
            else:
                print(f"[!] Slack response: {response.text}"); return f"Slack unexpected response: {response.text}"
        except requests.exceptions.RequestException as e:
            print(f"[!] Slack send error: {e}"); return f"Failed Slack send: {e}"
        except Exception as e:
            print(f"[!] Slack unexpected error: {e}"); return f"Unexpected Slack error: {e}"

    # --- JIRA HELPER FUNCTIONS (Unchanged) ---
    def get_jira_client(user):
        if not user.jira_credentials: return None
        creds = user.jira_credentials
        try:
            jira_client = JIRA(server=creds.jira_url, basic_auth=(creds.email, creds.api_token))
            jira_client.server_info()
            print("[*] Jira client initialized successfully.")
            return jira_client
        except JIRAError as e:
            print(f"[!] Failed to connect to Jira: {e.status_code} - {e.text}")
            flash(f"Jira Connection Error: {e.text}", "danger");
            return None
        except Exception as e:
            print(f"[!] Unexpected error initializing Jira client: {e}")
            flash(f"Jira Initialization Error: {e}", "danger");
            return None

    def create_jira_issues(user, action_items, project_key, issue_type_name):
        jira_client = get_jira_client(user)
        if not jira_client: return "Failed to connect to Jira. Check credentials."
        if not action_items: return "No action items to create."
        if not project_key or not issue_type_name: return "Jira Project/Issue Type required."

        issues_created = 0;
        failed_items = []
        for item in action_items:
            summary = item.get('task', 'Untitled Meeting Task')
            description = f"Assignee: {item.get('assignee', 'Unassigned')}\nDue Date: {item.get('due_date', 'Not specified')}"
            issue_dict = {'project': {'key': project_key}, 'summary': summary, 'description': description,
                          'issuetype': {'name': issue_type_name}}
            try:
                new_issue = jira_client.create_issue(fields=issue_dict)
                print(f"[*] Created Jira issue: {new_issue.key}");
                issues_created += 1
            except JIRAError as e:
                print(f"[!] Failed Jira issue '{summary}': {e.status_code} - {e.text}");
                failed_items.append(summary)
            except Exception as e:
                print(f"[!] Unexpected error on Jira issue '{summary}': {e}");
                failed_items.append(summary)

        if not failed_items:
            return f"{issues_created} Jira issues created in {project_key}."
        else:
            return f"Created {issues_created} issues. Failed for: {', '.join(failed_items)}."

    # --- ROUTES ---
    @app.route('/')
    @app.route('/home')
    @login_required
    def home():
        trello_client = get_trello_client(current_user)
        boards = trello_client.list_boards() if trello_client else []
        return render_template('index.html', trello_boards=boards)

    # --- GET_LISTS FUNCTION RESTORED ---
    @app.route('/get_lists/<board_id>')
    @login_required
    def get_lists(board_id):
        trello_client = get_trello_client(current_user)
        if not trello_client:
            return jsonify({"error": "Trello not connected"}), 400
        try:
            board = trello_client.get_board(board_id)
            lists = [{"id": lst.id, "name": lst.name} for lst in board.list_lists()]
            return jsonify(lists)
        except Exception as e:
            print(f"[!] Error fetching Trello lists: {e}")
            return jsonify({"error": str(e)}), 500

    # ----------------------------------

    # --- JIRA DATA ROUTES (Unchanged) ---
    @app.route('/get_jira_projects')
    @login_required
    def get_jira_projects():
        jira_client = get_jira_client(current_user)
        if not jira_client: return jsonify({"error": "Jira not connected or credentials invalid."}), 400
        try:
            projects = jira_client.projects()
            project_list = [{"key": p.key, "name": p.name} for p in projects]
            return jsonify(project_list)
        except JIRAError as e:
            print(f"[!] JIRAError fetching projects: {e.text}")
            return jsonify({"error": f"Jira API Error: {e.text}"}), 500
        except Exception as e:
            print(f"[!] Error fetching Jira projects: {e}")
            return jsonify({"error": "Could not fetch Jira projects."}), 500

    @app.route('/get_jira_issue_types/<project_key>')
    @login_required
    def get_jira_issue_types(project_key):
        jira_client = get_jira_client(current_user)
        if not jira_client: return jsonify({"error": "Jira not connected or credentials invalid."}), 400
        try:
            project = jira_client.project(project_key)
            issue_types = project.issueTypes
            issue_type_list = [{"id": it.id, "name": it.name, "subtask": it.subtask} for it in issue_types]
            return jsonify(issue_type_list)
        except JIRAError as e:
            print(f"[!] JIRAError fetching issue types: {e.text}")
            return jsonify({"error": f"Jira API Error: {e.text}"}), 500
        except Exception as e:
            print(f"[!] Error fetching Jira issue types: {e}")
            return jsonify({"error": f"Could not fetch issue types for {project_key}."}), 500

    @app.route('/analyze', methods=['POST'])
    @login_required
    def analyze():
        # ... (Unchanged) ...
        transcript_text = request.form.get('transcript')
        analysis_result, notification = None, None
        if transcript_text:
            analysis_result = analyze_transcript_with_ai(transcript_text)
            if analysis_result and not analysis_result.get('error'):
                automation_messages = []
                action_items_list = analysis_result.get('action_items', [])
                # Email Automation
                if request.form.get('send_email') == 'true' and current_user.team:
                    recipients = [m.email for m in current_user.team.members if m.email]
                    if recipients:
                        automation_messages.append(f"Email: {send_summary_email(recipients, analysis_result)}")
                    else:
                        automation_messages.append("Email: No emails in team.")
                elif request.form.get('send_email') == 'true':
                    automation_messages.append("Email: Requires team.")
                # Trello Automation
                if request.form.get('create_trello') == 'true' and current_user.trello_credentials:
                    t_client = get_trello_client(current_user)
                    b_id, l_id = request.form.get('trello_board_id'), request.form.get('trello_list_id')
                    if t_client and b_id and l_id:
                        automation_messages.append(
                            f"Trello: {create_trello_cards(t_client, b_id, l_id, action_items_list, current_user.id)}")
                    elif not b_id or not l_id:
                        automation_messages.append("Trello: Board/List missing.")
                    else:
                        automation_messages.append("Trello: Client error.")
                elif request.form.get('create_trello') == 'true':
                    automation_messages.append("Trello: Not connected.")
                # Slack Automation
                if request.form.get(
                        'send_slack') == 'true' and current_user.team and current_user.team.slack_webhook_url:
                    automation_messages.append(f"Slack: {send_to_slack(current_user.team, analysis_result)}")
                elif request.form.get('send_slack') == 'true':
                    if not current_user.team:
                        automation_messages.append("Slack: Requires team.")
                    else:
                        automation_messages.append("Slack: Not connected.")
                # JIRA Automation
                if request.form.get('create_jira') == 'true' and current_user.jira_credentials:
                    jira_project_key = request.form.get('jira_project_key')
                    jira_issue_type_name = request.form.get('jira_issue_type_name')
                    if not jira_project_key or not jira_issue_type_name:
                        automation_messages.append("Jira: Project and Issue Type must be selected.")
                    else:
                        jira_status = create_jira_issues(current_user, action_items_list, jira_project_key,
                                                         jira_issue_type_name)
                        automation_messages.append(f"Jira: {jira_status}")
                elif request.form.get('create_jira') == 'true':
                    automation_messages.append("Jira: Integration not connected.")
                # Notification logic
                if automation_messages:
                    overall_type = "success"
                    for msg in automation_messages:
                        if "Failed" in msg or "Error" in msg or "Invalid" in msg or "must be" in msg or "not connected" in msg or "missing" in msg:
                            overall_type = "danger";
                            break
                    notification = {"type": overall_type, "message": " | ".join(automation_messages)}
            elif analysis_result and analysis_result.get('error'):
                notification = {"type": "danger", "message": f"AI Error: {analysis_result['error']}"}

        trello_client = get_trello_client(current_user)
        boards = trello_client.list_boards() if trello_client else []
        return render_template('index.html', analysis=analysis_result, transcript=transcript_text,
                               notification=notification, trello_boards=boards)

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        # ... (unchanged) ...
        if current_user.is_authenticated: return redirect(url_for('home'))
        if request.method == 'POST':
            username, email, password = request.form.get('username'), request.form.get('email'), request.form.get(
                'password')
            if User.query.filter((User.username == username) | (User.email == email)).first():
                flash('Username or Email exists.', 'danger');
                return redirect(url_for('register'))
            user = User(username=username, email=email, password=password);
            db.session.add(user);
            db.session.commit()
            flash('Account created! Please log in.', 'success');
            return redirect(url_for('login'))
        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        # ... (unchanged) ...
        if current_user.is_authenticated: return redirect(url_for('home'))
        if request.method == 'POST':
            email, password = request.form.get('email'), request.form.get('password')
            user = User.query.filter_by(email=email).first()
            if user and user.verify_password(password):
                login_user(user, remember=True);
                next_page = request.args.get('next')
                return redirect(next_page or url_for('home'))
            else:
                flash('Login failed.', 'danger')
        return render_template('login.html')

    @app.route('/logout')
    def logout():
        # ... (unchanged) ...
        logout_user();
        return redirect(url_for('login'))

    # --- ADD THIS MISSING ROUTE ---
    @app.route('/team')
    @login_required
    def team():
        return render_template('team.html')

    # -----------------------------

    @app.route('/create_team', methods=['POST'])
    @login_required
    def create_team():
        # ... (unchanged) ...
        team_name = request.form.get('team_name')
        if team_name:
            if current_user.team: flash('Already in a team.', 'warning'); return redirect(url_for('team'))
            new_team = Team(name=team_name, owner_id=current_user.id);
            db.session.add(new_team)
            current_user.team = new_team;
            db.session.commit()
            flash(f'Team "{team_name}" created!', 'success')
        else:
            flash('Team name empty.', 'danger')
        return redirect(url_for('team'))

    @app.route('/invite', methods=['POST'])
    @login_required
    def invite():
        # ... (unchanged) ...
        if not current_user.team: flash('Must be in team.', 'danger'); return redirect(url_for('team'))
        email = request.form.get('email');
        user_to_invite = User.query.filter_by(email=email).first()
        if user_to_invite:
            if user_to_invite.team:
                flash(f'{user_to_invite.username} already in team.', 'warning')
            elif user_to_invite == current_user:
                flash('Cannot invite self.', 'warning')
            else:
                user_to_invite.team = current_user.team; db.session.commit(); flash(f'{user_to_invite.username} added.',
                                                                                    'success')
        else:
            flash('User not found.', 'danger')
        return redirect(url_for('team'))

    @app.route('/integrations')
    @login_required
    def integrations():
        # ... (unchanged) ...
        return render_template('integrations.html')

    # --- TRELLO ROUTES (Unchanged) ---
    @app.route('/trello/connect')
    @login_required
    def trello_connect():
        # ... (unchanged) ...
        app_name = "AI Agent";
        expiration = "never";
        scope = "read,write"
        if not TRELLO_API_KEY: flash('Trello Key missing.', 'danger'); return redirect(url_for('integrations'))
        auth_url = f"https://trello.com/1/authorize?key={TRELLO_API_KEY}&name={app_name}&expiration={expiration}&response_type=token&scope={scope}"
        return render_template('connect_trello.html', auth_url=auth_url)

    @app.route('/trello/save_token', methods=['POST'])
    @login_required
    def trello_save_token():
        # ... (unchanged) ...
        access_token = request.form.get('pin')
        if not access_token: flash('Token required.', 'danger'); return redirect(url_for('trello_connect'))
        if not TRELLO_API_KEY or not TRELLO_API_SECRET: flash('Trello Key/Secret missing.', 'danger'); return redirect(
            url_for('integrations'))
        try:
            client = TrelloClient(api_key=TRELLO_API_KEY, api_secret=TRELLO_API_SECRET, token=access_token)
            trello_user = client.get_member('me')
            creds = TrelloCredentials.query.filter_by(user_id=current_user.id).first() or TrelloCredentials(
                user_id=current_user.id)
            creds.token, creds.trello_username = access_token, trello_user.full_name
            db.session.add(creds);
            db.session.commit();
            flash('Trello connected!', 'success');
            return redirect(url_for('integrations'))
        except Exception as e:
            flash(f'Trello failed: {e}', 'danger'); db.session.rollback(); return redirect(url_for('trello_connect'))

    @app.route('/trello/disconnect')
    @login_required
    def trello_disconnect():
        # ... (unchanged) ...
        creds = TrelloCredentials.query.filter_by(user_id=current_user.id).first()
        if creds: db.session.delete(creds); db.session.commit(); flash('Trello disconnected.', 'success')
        return redirect(url_for('integrations'))

    # --- SLACK ROUTES (Unchanged) ---
    @app.route('/slack/connect', methods=['POST'])
    @login_required
    def slack_connect():
        # ... (unchanged) ...
        if not current_user.team: flash('Must be in team.', 'danger'); return redirect(url_for('integrations'))
        webhook_url = request.form.get('slack_webhook_url')
        if not webhook_url or not webhook_url.startswith('https://hooks.slack.com/services/'):
            flash('Invalid Slack URL.', 'danger');
            return redirect(url_for('integrations'))
        current_user.team.slack_webhook_url = webhook_url
        try:
            db.session.commit(); flash('Slack saved!', 'success')
        except Exception as e:
            db.session.rollback(); flash(f'Save failed: {e}', 'danger')
        return redirect(url_for('integrations'))

    @app.route('/slack/disconnect')
    @login_required
    def slack_disconnect():
        # ... (unchanged) ...
        if current_user.team and current_user.team.slack_webhook_url:
            current_user.team.slack_webhook_url = None
            try:
                db.session.commit(); flash('Slack disconnected.', 'success')
            except Exception as e:
                db.session.rollback(); flash(f'Disconnect failed: {e}', 'danger')
        else:
            flash('Slack not connected.', 'warning')
        return redirect(url_for('integrations'))

    # --- JIRA ROUTES (Unchanged) ---
    @app.route('/jira/connect', methods=['POST'])
    @login_required
    def jira_connect():
        # ... (unchanged) ...
        jira_url, email, api_token = request.form.get('jira_url'), request.form.get('jira_email'), request.form.get(
            'jira_api_token')
        if not all([jira_url, email, api_token]): flash('All Jira fields required.', 'danger'); return redirect(
            url_for('integrations'))
        if not jira_url.startswith('https://') or not jira_url.endswith('.atlassian.net'):
            flash('Invalid Jira URL.', 'danger');
            return redirect(url_for('integrations'))
        creds = JiraCredentials.query.filter_by(user_id=current_user.id).first() or JiraCredentials(
            user_id=current_user.id)
        creds.jira_url = jira_url.rstrip('/');
        creds.email = email;
        creds.api_token = api_token
        try:
            # Add verification call here if desired
            db.session.add(creds);
            db.session.commit();
            flash('Jira saved!', 'success')
        except Exception as e:
            db.session.rollback(); flash(f'Jira save failed: {e}', 'danger')
        return redirect(url_for('integrations'))

    @app.route('/jira/disconnect')
    @login_required
    def jira_disconnect():
        # ... (unchanged) ...
        creds = JiraCredentials.query.filter_by(user_id=current_user.id).first()
        if creds:
            try:
                db.session.delete(creds); db.session.commit(); flash('Jira disconnected.', 'success')
            except Exception as e:
                db.session.rollback(); flash(f'Jira disconnect failed: {e}', 'danger')
        else:
            flash('Jira not connected.', 'warning')
        return redirect(url_for('integrations'))

    return app


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        if not os.environ.get('DATABASE_URL'):
            db.create_all()
    app.run(debug=True)
