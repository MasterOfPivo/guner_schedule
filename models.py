from flask_sqlalchemy import SQLAlchemy

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
