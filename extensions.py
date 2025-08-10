from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager

# Create extension instances without initializing them
db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
