from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

# 🐾 Модель для кэширования расписаний в БД
class ParsedSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), unique=True, nullable=False)
    mtime = db.Column(db.Float, nullable=False)
    data_json = db.Column(db.Text, nullable=False)
    group_mapping_json = db.Column(db.Text, nullable=False)

    def __init__(self, **kwargs):
        super(ParsedSchedule, self).__init__(**kwargs)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_confirmed = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=db.func.now())

    def __init__(self, username: str, email: str, password_hash: str, is_confirmed: bool = False, is_admin: bool = False):
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.is_confirmed = is_confirmed
        self.is_admin = is_admin

class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    period = db.Column(db.String(100), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.now())
    
    user = db.relationship('User', backref=db.backref('receipts', lazy=True))

    def __init__(self, **kwargs):
        super(Receipt, self).__init__(**kwargs)
