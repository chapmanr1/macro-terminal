from flask import Blueprint, render_template, jsonify
from .database import db
from .models import Example

main = Blueprint("main", __name__)


@main.route("/")
def index():
    return render_template("index.html")


@main.route("/api/examples")
def get_examples():
    examples = Example.query.all()
    return jsonify([{"id": e.id, "name": e.name, "created_at": str(e.created_at)} for e in examples])


@main.route("/health")
def health():
    return jsonify({"status": "ok"})
