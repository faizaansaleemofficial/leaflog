import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlencode
from urllib.request import urlopen

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash
from supabase import create_client
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__, static_folder="public", static_url_path="")
app.secret_key = os.environ["SECRET_KEY"]
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # reject requests over 4 MB

SUPABASE_URL = os.environ["SUPABASE_URL"].strip().strip("'\"")
SUPABASE_KEY = os.environ["SUPABASE_KEY"].strip().strip("'\"")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

PHOTO_BUCKET = "plant-photos"
MAX_PHOTO_BYTES = 3 * 1024 * 1024


def login_required(view):
    """Redirect to login if the user isn't signed in."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.errorhandler(413)
def file_too_large(e):
    flash("That upload is too large. Photos must be under 3 MB.", "error")
    return redirect(url_for("dashboard"))


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


# ---------- Weather (Open-Meteo API) ----------

def fetch_json(url, params):
    """Call a web API and return its JSON answer."""
    with urlopen(url + "?" + urlencode(params), timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def geocode_city(name):
    """Turn a city name into coordinates."""
    data = fetch_json("https://geocoding-api.open-meteo.com/v1/search",
                      {"name": name, "count": 1, "language": "en", "format": "json"})
    results = data.get("results")
    if not results:
        return None
    place = results[0]
    label = place["name"]
    if place.get("country"):
        label += ", " + place["country"]
    return {"city": label, "latitude": place["latitude"], "longitude": place["longitude"]}


def get_weather(lat, lon):
    """Get today's and tomorrow's forecast."""
    data = fetch_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m",
        "daily": "precipitation_sum,temperature_2m_max",
        "timezone": "auto",
        "forecast_days": 2,
    })
    daily = data["daily"]
    current = data.get("current") or {}
    return {
        "temp_now": round(current.get("temperature_2m") or 0),
        "max_today": round(daily["temperature_2m_max"][0] or 0),
        "rain_today": round(daily["precipitation_sum"][0] or 0, 1),
        "rain_tomorrow": round(daily["precipitation_sum"][1] or 0, 1),
    }


def weather_advice(w):
    if w["rain_today"] >= 2:
        return "Rain expected today. Skip watering outdoor plants."
    if w["rain_tomorrow"] >= 2:
        return "Rain is forecast tomorrow. Outdoor plants can wait a day."
    if w["max_today"] >= 35:
        return "Very hot today. Check the soil and water in the evening."
    return "Dry day ahead. A good time to water."


def weather_context():
    """Everything the dashboard needs to show the weather card."""
    rows = (supabase.table("users").select("city,latitude,longitude")
            .eq("id", session["user_id"]).execute().data)
    if not rows or rows[0].get("latitude") is None:
        return {"weather": None, "city": None}

    user = rows[0]
    try:
        weather = get_weather(user["latitude"], user["longitude"])
        weather["advice"] = weather_advice(weather)
    except Exception as e:
        print("Weather error:", e)
        weather = {"error": True}
    return {"weather": weather, "city": user["city"]}


@app.route("/settings/city", methods=["POST"])
@login_required
def set_city():
    name = request.form.get("city", "").strip()
    if not name:
        flash("Please enter a city.", "error")
        return redirect(url_for("dashboard"))

    try:
        place = geocode_city(name)
    except Exception as e:
        print("Geocoding error:", e)
        flash("Could not reach the weather service. Please try again.", "error")
        return redirect(url_for("dashboard"))

    if not place:
        flash("City not found. Try a different spelling.", "error")
        return redirect(url_for("dashboard"))

    supabase.table("users").update(place).eq("id", session["user_id"]).execute()
    flash("Weather location set to " + place["city"] + ".", "success")
    return redirect(url_for("dashboard"))


# ---------- Photo uploads (Supabase Storage) ----------

def detect_image_type(data):
    """Check the file's real contents, not just its name."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None, None


def save_photo(file):
    """Upload a photo. Returns (public_url, error). (None, None) if no file was chosen."""
    if not file or not file.filename:
        return None, None

    data = file.read()
    if len(data) > MAX_PHOTO_BYTES:
        return None, "Photo must be smaller than 3 MB."

    ext, mimetype = detect_image_type(data)
    if not ext:
        return None, "Photo must be a PNG, JPG or WebP image."

    # Random file name, so users can't overwrite each other's files
    path = f"{session['user_id']}/{uuid.uuid4().hex}.{ext}"
    try:
        supabase.storage.from_(PHOTO_BUCKET).upload(path, data, {"content-type": mimetype})
    except Exception as e:
        print("Photo upload error:", e)
        return None, "Photo upload failed. Please try again."

    return supabase.storage.from_(PHOTO_BUCKET).get_public_url(path), None


def delete_photo(url):
    """Remove a stored photo (best effort)."""
    if not url:
        return
    marker = "/object/public/" + PHOTO_BUCKET + "/"
    if marker not in url:
        return
    path = url.split(marker, 1)[1].split("?")[0]
    try:
        supabase.storage.from_(PHOTO_BUCKET).remove([path])
    except Exception as e:
        print("Photo delete error:", e)


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


def parse_ts(value):
    """Turn a Supabase timestamp string into a datetime."""
    value = value.replace("Z", "+00:00")
    value = re.sub(r"\.(\d+)", lambda m: "." + m.group(1).ljust(6, "0")[:6], value)
    return datetime.fromisoformat(value)


@app.route("/dashboard")
@login_required
def dashboard():
    plants = (supabase.table("plants").select("*")
              .eq("user_id", session["user_id"])
              .order("created_at", desc=True)
              .execute().data)

    logs = []
    if plants:
        ids = [p["id"] for p in plants]
        logs = (supabase.table("watering_logs").select("plant_id,watered_at")
                .in_("plant_id", ids)
                .order("watered_at", desc=True)
                .execute().data)

    # Newest log for each plant (logs are already newest-first)
    last_watered = {}
    for log in logs:
        last_watered.setdefault(log["plant_id"], log["watered_at"])

    today = datetime.now(timezone.utc).date()
    for plant in plants:
        last = last_watered.get(plant["id"])
        start = parse_ts(last) if last else parse_ts(plant["created_at"])
        due = start.date() + timedelta(days=plant["watering_interval_days"])
        days_left = (due - today).days

        plant["last_watered"] = parse_ts(last).strftime("%d %b %Y") if last else None
        if days_left < 0:
            plant["status"] = f"Overdue by {-days_left} day{'s' if days_left != -1 else ''}"
            plant["status_class"] = "overdue"
        elif days_left == 0:
            plant["status"] = "Water today"
            plant["status_class"] = "due"
        else:
            plant["status"] = f"Next watering in {days_left} day{'s' if days_left != 1 else ''}"
            plant["status_class"] = "ok"

    return render_template("dashboard.html", plants=plants, **weather_context())


@app.route("/plants/add", methods=["GET", "POST"])
@login_required
def add_plant():
    if request.method == "POST":
        data, error = read_plant_form()
        photo_url = None
        if not error:
            photo_url, error = save_photo(request.files.get("photo"))
        if error:
            flash(error, "error")
            return render_template("plant_form.html", plant=request.form, mode="add")

        data["user_id"] = session["user_id"]
        if photo_url:
            data["photo_url"] = photo_url
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
        photo_url = None
        if not error:
            photo_url, error = save_photo(request.files.get("photo"))
        if error:
            flash(error, "error")
            return render_template("plant_form.html", plant=request.form, mode="edit")

        if photo_url:
            delete_photo(plant.get("photo_url"))  # remove the old picture
            data["photo_url"] = photo_url

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
    plant = get_own_plant(plant_id)
    if plant:
        delete_photo(plant.get("photo_url"))
        (supabase.table("plants").delete()
         .eq("id", plant_id)
         .eq("user_id", session["user_id"])
         .execute())
        flash("Plant deleted.", "success")
    else:
        flash("Plant not found.", "error")
    return redirect(url_for("dashboard"))


# ---------- Watering logs ----------

@app.route("/plants/<int:plant_id>")
@login_required
def plant_detail(plant_id):
    plant = get_own_plant(plant_id)
    if not plant:
        flash("Plant not found.", "error")
        return redirect(url_for("dashboard"))

    logs = (supabase.table("watering_logs").select("*")
            .eq("plant_id", plant_id)
            .order("watered_at", desc=True)
            .execute().data)
    for log in logs:
        log["when"] = parse_ts(log["watered_at"]).strftime("%d %b %Y, %H:%M UTC")

    return render_template("plant_detail.html", plant=plant, logs=logs)


@app.route("/plants/<int:plant_id>/water", methods=["POST"])
@login_required
def water_plant(plant_id):
    plant = get_own_plant(plant_id)
    if not plant:
        flash("Plant not found.", "error")
        return redirect(url_for("dashboard"))

    note = request.form.get("note", "").strip() or None
    supabase.table("watering_logs").insert({
        "plant_id": plant_id,
        "note": note,
        "watered_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    flash(f"Logged watering for {plant['name']}!", "success")

    if request.form.get("from") == "detail":
        return redirect(url_for("plant_detail", plant_id=plant_id))
    return redirect(url_for("dashboard"))


@app.route("/logs/<int:log_id>/delete", methods=["POST"])
@login_required
def delete_log(log_id):
    rows = supabase.table("watering_logs").select("*").eq("id", log_id).execute().data
    # Only allow it if the log belongs to one of this user's plants
    if not rows or not get_own_plant(rows[0]["plant_id"]):
        flash("Entry not found.", "error")
        return redirect(url_for("dashboard"))

    supabase.table("watering_logs").delete().eq("id", log_id).execute()
    flash("Watering entry deleted.", "success")
    return redirect(url_for("plant_detail", plant_id=rows[0]["plant_id"]))


if __name__ == "__main__":
    app.run(debug=True)