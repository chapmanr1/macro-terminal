from flask import Flask, render_template, jsonify
from regime_engine import get_current_regime
from fred_data import get_fred_data
from news_feed import get_news

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/regime")
def regime():
    return jsonify(get_current_regime())


@app.route("/api/fred")
def fred():
    return jsonify(get_fred_data())


@app.route("/api/news")
def news():
    return jsonify(get_news())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
