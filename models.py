from extensions import db, bcrypt
from flask_login import UserMixin

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'))
    trello_credentials = db.relationship('TrelloCredentials', backref='user', uselist=False, cascade="all, delete-orphan")
    created_cards = db.relationship('TrelloCard', backref='creator', lazy=True)

    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')

    @password.setter
    def password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def verify_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    owner_id = db.Column(db.Integer, nullable=False)
    members = db.relationship('User', backref='team', lazy=True)

class TrelloCredentials(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    token = db.Column(db.String(200), nullable=False)
    trello_username = db.Column(db.String(100))

# New table to track created Trello cards
class TrelloCard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.String(100), unique=True, nullable=False) # The ID from Trello
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) # Who created it
    board_id = db.Column(db.String(100), nullable=False)
    list_id = db.Column(db.String(100), nullable=False)
    task_description = db.Column(db.Text, nullable=False)
    assignee = db.Column(db.String(150))
    due_date_str = db.Column(db.String(100)) # The text due date from the AI
