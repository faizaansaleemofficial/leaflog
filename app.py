import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def login_required(view):
    """Redirect to login if the user isn't signed in."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("signup.html")

        # Check for duplicates
        if supabase.table("users").select("id").eq("username", username).execute().data:
            flash("That username is taken.", "error")
            return render_template("signup.html")
        if supabase.table("users").select("id").eq("email", email).execute().data:
            flash("That email is already registered.", "error")
            return render_template("signup.html")

        supabase.table("users").insert({
            "username": username,
            "email": email,
            "password_hash": generate_password_hash(password),
        }).execute()

        flash("Account created! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        result = supabase.table("users").select("*").eq("username", username).execute()
        user = result.data[0] if result.data else None

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    app.run(debug=True)