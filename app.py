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


# ---------- Auth ----------

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

        try:
            # The database itself enforces unique usernames and emails
            supabase.table("users").insert({
                "username": username,
                "email": email,
                "password_hash": generate_password_hash(password),
            }).execute()
        except Exception as e:
            detail = str(e).lower()
            if "23505" in detail or "duplicate" in detail:
                if "email" in detail:
                    flash("That email is already registered. Try logging in.", "error")
                else:
                    flash("That username is taken. Try logging in, or pick another.", "error")
            else:
                print("Sign-up error:", e)
                flash("Something went wrong. Please try again.", "error")
            return render_template("signup.html")

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


# ---------- Plants (CRUD) ----------

def read_plant_form():
    """Validate the plant form. Returns (data, error_message)."""
    name = request.form.get("name", "").strip()
    if not name:
        return None, "Plant name is required."

    try:
        interval = int(request.form.get("watering_interval_days", "7"))
    except ValueError:
        return None, "Watering interval must be a number."
    if not 1 <= interval <= 365:
        return None, "Watering interval must be between 1 and 365 days."

    data = {
        "name": name,
        "species": request.form.get("species", "").strip() or None,
        "location": request.form.get("location", "").strip() or None,
        "watering_interval_days": interval,
        "notes": request.form.get("notes", "").strip() or None,
    }
    return data, None


def get_own_plant(plant_id):
    """Fetch a plant only if it belongs to the logged-in user."""
    result = (supabase.table("plants").select("*")
              .eq("id", plant_id)
              .eq("user_id", session["user_id"])
              .execute())
    return result.data[0] if result.data else None


@app.route("/dashboard")
@login_required
def dashboard():
    plants = (supabase.table("plants").select("*")
              .eq("user_id", session["user_id"])
              .order("created_at", desc=True)
              .execute().data)
    return render_template("dashboard.html", plants=plants)


@app.route("/plants/add", methods=["GET", "POST"])
@login_required
def add_plant():
    if request.method == "POST":
        data, error = read_plant_form()
        if error:
            flash(error, "error")
            return render_template("plant_form.html", plant=request.form, mode="add")

        data["user_id"] = session["user_id"]
        supabase.table("plants").insert(data).execute()
        flash("Plant added!", "success")
        return redirect(url_for("dashboard"))

    return render_template("plant_form.html", plant={}, mode="add")


@app.route("/plants/<int:plant_id>/edit", methods=["GET", "POST"])
@login_required
def edit_plant(plant_id):
    plant = get_own_plant(plant_id)
    if not plant:
        flash("Plant not found.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        data, error = read_plant_form()
        if error:
            flash(error, "error")
            return render_template("plant_form.html", plant=request.form, mode="edit")

        (supabase.table("plants").update(data)
         .eq("id", plant_id)
         .eq("user_id", session["user_id"])
         .execute())
        flash("Plant updated!", "success")
        return redirect(url_for("dashboard"))

    return render_template("plant_form.html", plant=plant, mode="edit")


@app.route("/plants/<int:plant_id>/delete", methods=["POST"])
@login_required
def delete_plant(plant_id):
    (supabase.table("plants").delete()
     .eq("id", plant_id)
     .eq("user_id", session["user_id"])
     .execute())
    flash("Plant deleted.", "success")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    app.run(debug=True)