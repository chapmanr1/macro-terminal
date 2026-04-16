from .database import db
from datetime import datetime


class Example(db.Model):
    __tablename__ = "examples"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Example {self.name}>"
