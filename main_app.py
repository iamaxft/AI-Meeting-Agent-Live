import os
import json
import smtplib
from email.mime.text import MIMEText
import requests
from jira import JIRA # Make sure this is imported
from jira.exceptions import JIRAError # Import specific Jira errors

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
import google.generativeai as genai
from trello import TrelloClient

from extensions import db, bcrypt, login_manager
from models import User, Team, TrelloCredentials, TrelloCard, JiraCredentials
from dotenv import load_dotenv

load_dotenv()
# --- CONFIGURATION (Unchanged) ---
# ... (Keep your API keys and secret key loading) ...
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
    # ... (Keep database config) ...
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        if database_url.startswith("postgres://"): database_url = database_url.replace("postgres://", "postgresql://", 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    else: app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'

    db.init_app(app); bcrypt.init_app(app); login_manager.init_app(app)
    login_manager.login_view = 'login'

    @login_manager.user_loader
    def load_user(user_id): return User.query.get(int(user_id))

    # --- AI MODEL AND HELPER FUNCTIONS (Unchanged) ---
    # ... (Keep AI, Trello client, Email, Slack, Trello cards functions) ...
    try:
        if GEMINI_API_KEY:
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-2.5-flash')
            print("[*] Gemini AI configured.")
        else: model = None; print("[!] GEMINI_API_KEY missing.")
    except Exception as e: model = None; print(f"[!] Gemini AI Error: {e}")

    def analyze_transcript_with_ai(transcript_text):
        if not model: return {"error": "AI model not configured."}
        prompt = f"""...""" # Your prompt
        try:
            response = model.generate_content(prompt); print(f"Raw AI: {response.text}")
            json_text = response.text.strip().replace('```json', '').replace('```', '').strip()
            if not json_text: return {"error": "AI empty response."}
            return json.loads(json_text)
        except json.JSONDecodeError as e: return {"error": f"AI JSON Parse Error: {e}. Raw: '{response.text}'"}
        except Exception as e: return {"error": f"AI Error: {e}"}

    def get_trello_client(user):
        if user.trello_credentials: return TrelloClient(api_key=TRELLO_API_KEY, api_secret=TRELLO_API_SECRET, token=user.trello_credentials.token)
        return None

    def send_summary_email(recipients, analysis):
        # ... (Keep email function logic) ...
         return "Email sent." # Placeholder

    def create_trello_cards(client, board_id, list_id, action_items, user_id):
        # ... (Keep trello card function logic) ...
         return "Trello cards created." # Placeholder

    def send_to_slack(team, analysis):
        # ... (Keep slack function logic) ...
        # Ensure blocks definition is restored here
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "📝 Meeting Summary", "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text": analysis.get('summary', 'N/A')}},
            {"type": "divider"}
        ]
        if analysis.get('decisions'): blocks.extend([{"type": "section", "text": {"type": "mrkdwn", "text": "*⚖️ Decisions:*\n" + "\n".join([f"• {d}" for d in analysis['decisions']])}}, {"type": "divider"}])
        if analysis.get('action_items'): blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*✅ Action Items:*\n" + "\n".join([f"• *Task:* {i.get('task', 'N/A')} | *Assignee:* {i.get('assignee', 'N/A')} | *Due:* {i.get('due_date', 'N/A')}" for i in analysis['action_items']])}})
        # ... (rest of Slack function) ...
        return "Slack message sent." # Placeholder

    # --- JIRA HELPER FUNCTIONS ---
    def get_jira_client(user):
        """Initializes and returns a JIRA client instance using stored credentials."""
        if not user.jira_credentials:
            return None
        creds = user.jira_credentials
        try:
            jira_client = JIRA(
                server=creds.jira_url,
                basic_auth=(creds.email, creds.api_token)
            )
            # Test connection by fetching server info (optional but good)
            jira_client.server_info()
            print("[*] Jira client initialized successfully.")
            return jira_client
        except JIRAError as e:
            print(f"[!] Failed to connect to Jira: {e.status_code} - {e.text}")
            flash(f"Jira Connection Error: {e.text}", "danger") # Flash error to user
            return None
        except Exception as e:
            print(f"[!] Unexpected error initializing Jira client: {e}")
            flash(f"Jira Initialization Error: {e}", "danger")
            return None

    # --- UPDATED JIRA ISSUE CREATION FUNCTION ---
    def create_jira_issues(user, action_items, project_key, issue_type_name): # Added issue_type_name
        """Connects to Jira and creates issues for each action item."""
        jira_client = get_jira_client(user)
        if not jira_client:
            return "Failed to connect to Jira. Check credentials." # More specific error
        if not action_items:
            return "No action items to create."
        if not project_key or not issue_type_name:
             return "Jira Project Key and Issue Type Name are required."

        issues_created = 0
        failed_items = []
        for item in action_items:
            summary = item.get('task', 'Untitled Meeting Task')
            description = f"Assignee: {item.get('assignee', 'Unassigned')}\nDue Date: {item.get('due_date', 'Not specified')}"
            issue_dict = {
                'project': {'key': project_key},
                'summary': summary,
                'description': description,
                'issuetype': {'name': issue_type_name}, # Use selected issue type
            }
            try:
                new_issue = jira_client.create_issue(fields=issue_dict)
                print(f"[*] Created Jira issue: {new_issue.key}")
                issues_created += 1
            except JIRAError as e:
                 print(f"[!] Failed to create Jira issue for task '{summary}': {e.status_code} - {e.text}")
                 failed_items.append(summary)
            except Exception as e:
                 print(f"[!] Unexpected error creating Jira issue for task '{summary}': {e}")
                 failed_items.append(summary)

        if not failed_items:
            return f"{issues_created} Jira issues created in {project_key}."
        else:
            return f"Created {issues_created} issues. Failed for: {', '.join(failed_items)}."
    # -------------------------------

    # --- ROUTES ---
    @app.route('/')
    @app.route('/home')
    @login_required
    def home():
        # ... (unchanged) ...
        trello_client = get_trello_client(current_user)
        boards = trello_client.list_boards() if trello_client else []
        return render_template('index.html', trello_boards=boards)

    @app.route('/get_lists/<board_id>')
    @login_required
    def get_lists(board_id):
        # ... (unchanged) ...
         return jsonify([]) # Placeholder

    # --- NEW JIRA DATA ROUTES ---
    @app.route('/get_jira_projects')
    @login_required
    def get_jira_projects():
        jira_client = get_jira_client(current_user)
        if not jira_client:
            return jsonify({"error": "Jira not connected or credentials invalid."}), 400
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
        if not jira_client:
            return jsonify({"error": "Jira not connected or credentials invalid."}), 400
        try:
            # Fetch project first to ensure it exists and get its ID if needed by some API versions
            project = jira_client.project(project_key)
            issue_types = project.issueTypes # Simpler way if available
            # Or use: issue_types = jira_client.issue_types_for_project(project_key) if the above fails
            issue_type_list = [{"id": it.id, "name": it.name, "subtask": it.subtask} for it in issue_types]
            return jsonify(issue_type_list)
        except JIRAError as e:
             print(f"[!] JIRAError fetching issue types for {project_key}: {e.text}")
             return jsonify({"error": f"Jira API Error: {e.text}"}), 500
        except Exception as e:
            print(f"[!] Error fetching Jira issue types for {project_key}: {e}")
            return jsonify({"error": f"Could not fetch issue types for project {project_key}."}), 500
    # --------------------------

    @app.route('/analyze', methods=['POST'])
    @login_required
    def analyze():
        transcript_text = request.form.get('transcript')
        analysis_result, notification = None, None
        if transcript_text:
            analysis_result = analyze_transcript_with_ai(transcript_text)
            if analysis_result and not analysis_result.get('error'):
                automation_messages = []
                action_items_list = analysis_result.get('action_items', [])

                # Email Automation (Unchanged logic)
                # ...
                # Trello Automation (Unchanged logic)
                # ...
                # Slack Automation (Unchanged logic)
                # ...

                # --- UPDATED JIRA AUTOMATION ---
                if request.form.get('create_jira') == 'true' and current_user.jira_credentials:
                    # Get selected project key and issue type name from the form
                    jira_project_key = request.form.get('jira_project_key')
                    jira_issue_type_name = request.form.get('jira_issue_type_name')

                    if not jira_project_key or not jira_issue_type_name:
                         automation_messages.append("Jira: Project and Issue Type must be selected.")
                    else:
                        # Pass selected values to the function
                        jira_status = create_jira_issues(current_user, action_items_list, jira_project_key, jira_issue_type_name)
                        automation_messages.append(f"Jira: {jira_status}")
                elif request.form.get('create_jira') == 'true':
                    automation_messages.append("Jira: Integration not connected.")
                # -------------------------------

                # Notification logic (Unchanged)
                if automation_messages:
                    overall_type = "success"
                    # ... (rest of notification logic) ...
                    notification = {"type": overall_type, "message": " | ".join(automation_messages)}
            elif analysis_result and analysis_result.get('error'):
                 notification = {"type": "danger", "message": f"AI Error: {analysis_result['error']}"}

        trello_client = get_trello_client(current_user)
        boards = trello_client.list_boards() if trello_client else []
        return render_template('index.html', analysis=analysis_result, transcript=transcript_text,
                               notification=notification, trello_boards=boards)

    # --- Other routes (register, login, etc. Unchanged) ---
    # ... (Keep all other routes like register, login, team, integrations, trello connect/disconnect, slack connect/disconnect, jira connect/disconnect) ...


    return app


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        if not os.environ.get('DATABASE_URL'):
            db.create_all()
    app.run(debug=True)
