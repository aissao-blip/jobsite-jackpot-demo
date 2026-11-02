"""Jobsite Jackpot — Portfolio Demo
Standalone Flask app. Local, self-contained, fictional data, no external services.

Run:
    python app.py
Then open http://localhost:5000  (redirects to the live board).

Admin:  http://localhost:5000/payout-board   (user: admin, pass: jackpot-dev by default)
"""
import os
from flask import Flask, redirect

import pb_db
from payout_board import payout_board_bp
from seed_demo import seed_if_empty, reset_demo


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.secret_key = os.environ.get("SECRET_KEY", "jobsite-jackpot-demo")
    app.register_blueprint(payout_board_bp)

    @app.route("/")
    def home():
        return redirect("/payout-board/live")

    # Demo convenience: one-click restore of the fictional data (local demo only).
    @app.route("/demo/reset", methods=["GET", "POST"])
    def demo_reset():
        n = reset_demo()
        return redirect("/payout-board/live?reset=" + str(n))

    return app


app = create_app()

if __name__ == "__main__":
    pb_db.init_db()
    n = seed_if_empty()
    port = int(os.environ.get("PORT", "5000"))
    print("\n" + "=" * 60)
    print("  JOBSITE JACKPOT — Portfolio Demo")
    print("=" * 60)
    if n:
        print(f"  Seeded {n} fictional creators.")
    print(f"  Live board : http://localhost:{port}/payout-board/live")
    print(f"  Admin panel: http://localhost:{port}/payout-board  (admin / jackpot-dev)")
    print(f"  Reset data : http://localhost:{port}/demo/reset")
    print("=" * 60 + "\n")
    app.run(host="127.0.0.1", port=port, debug=False)
