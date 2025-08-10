AI Meeting Agent & Automation Platform
This project is a full-stack SaaS application that automates the entire post-meeting workflow. It uses a Large Language Model (Google's Gemini) to analyze meeting transcripts, extracts structured data like summaries and action items, and then automates follow-up tasks by sending emails and creating cards in project management tools like Trello.

The application is built with a professional architecture, including a user and team management system, secure OAuth 2.0 integrations, and a background worker for persistent task monitoring, making it a powerful demonstration of modern AI and backend engineering skills.

Features
AI-Powered Analysis: Leverages the Gemini 1.5 Flash model to perform several NLP tasks on raw meeting transcripts:

Concise Summaries: Generates a one-paragraph summary of the meeting.

Key Decision Tracking: Extracts a bulleted list of all concrete decisions made.

Action Item Extraction: Identifies tasks, assignees, and due dates.

Full User & Team System: Secure user registration and login, with the ability for users to create teams and invite members.

Seamless Integrations: Uses a secure, professional OAuth flow to connect to third-party services like Trello, eliminating the need for users to manually handle API keys.

Automated Workflow Engine:

Email Automation: Automatically sends formatted summary emails to all team members.

Task Management Automation: Automatically creates new cards in a user's selected Trello board and list for each action item.

AI Accountability Agent: A background worker (worker.py) runs on a schedule to monitor the status of created Trello cards, providing a foundation for automated follow-ups and reminders.

Interactive Web Interface: A clean, user-friendly dashboard built with Flask and Jinja2 that allows for transcript submission, automation control, and team management.

Tech Stack
Language: Python 3

AI Model: Google Gemini

Backend: Flask, Flask-SQLAlchemy, Flask-Login, Flask-Bcrypt

Database: SQLite3

Task Management: Trello API (py-trello)

Scheduling: Schedule

Emailing: smtplib

Setup and Installation
Follow these steps to get the project running on your local machine.

1. Clone the Repository
git clone https://github.com/your-username/AI-Meeting-Agent.git
cd AI-Meeting-Agent

2. Create a Virtual Environment
# For Windows
python -m venv venv
venv\Scripts\activate

# For macOS/Linux
python3 -m venv venv
source venv/bin/activate

3. Install Dependencies
Install all the required libraries using the requirements.txt file.

pip install -r requirements.txt

4. Configure API Keys
Open the main_app.py and worker.py files and fill in your credentials in the CONFIGURATION section at the top:

GEMINI_API_KEY: Your key from Google AI Studio.

TRELLO_API_KEY & TRELLO_API_SECRET: Your Trello developer credentials.

SENDER_EMAIL & SENDER_PASSWORD: Your Gmail address and a 16-character Google App Password.

5. Set up Trello OAuth
Go to your Trello Power-Ups admin page and select your app.

In the "API key" tab, ensure the following are set:

Allowed origins: http://127.0.0.1:5000

Authorized redirect URIs: http://127.0.0.1:5000/trello/callback

Usage
The application requires two processes to be run simultaneously in separate terminals.

1. Run the Web Server
In your first terminal, run the main Flask application:

python main_app.py

This will start the web server. You can now go to http://127.0.0.1:5000 to register, log in, and use the application.

2. Run the Background Worker
In your second terminal, run the accountability agent:

python worker.py

This worker will run in the background, checking on the status of Trello cards every minute (for testing).