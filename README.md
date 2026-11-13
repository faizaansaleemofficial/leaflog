# 🌿 LeafLog

A plant care tracker that helps you remember when to water your plants.

**Live app:** YOUR-LIVE-LINK

## Problem
People forget when they last watered their plants, so plants dry out or get overwatered.

## Target audience
Students, apartment dwellers and beginners with a few houseplants.

## Features
- Sign up and log in (hashed passwords, Flask sessions)
- Add, view, edit and delete plants (CRUD)
- Log waterings with notes and see history per plant
- Due and overdue status on each plant
- Live weather advice using the Open-Meteo API (bonus)
- Plant photo uploads stored in Supabase Storage (bonus)
- Poster (bonus): see docs/poster.png

## Database logic
- users: id, username, email, password_hash, city, latitude, longitude
- plants: id, user_id, name, species, location, watering_interval_days, notes, photo_url
- watering_logs: id, plant_id, watered_at, note
- One user has many plants; one plant has many watering logs. All queries are filtered by the logged-in user.

## Tech stack
Flask (Python), Supabase (PostgreSQL and Storage), Vercel, HTML/CSS/JavaScript

## Run it locally
1. Clone the repo and create a virtual environment
2. Install packages: `python -m pip install -r requirements.txt`
3. Create a `.env` file with SECRET_KEY, SUPABASE_URL and SUPABASE_KEY
4. Run `python app.py` and open http://127.0.0.1:5000

## Security
Secrets are stored in environment variables, never in the repository (`.env` is listed in `.gitignore`).
