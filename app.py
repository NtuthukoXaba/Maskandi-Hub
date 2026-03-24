import os
import math
import secrets
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["DEBUG"] = os.environ.get("DEBUG", "False").lower() == "true"

DATABASE_URL = os.environ.get("DATABASE_URL")

ARTIST_UPLOAD_FOLDER = "static/uploads/artists"
SONG_UPLOAD_FOLDER = "static/uploads/songs"
VOTE_UPLOAD_FOLDER = "static/uploads/votes"
EVENT_UPLOAD_FOLDER = "static/uploads/events"
NEWS_UPLOAD_FOLDER = "static/uploads/news"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

app.config["ARTIST_UPLOAD_FOLDER"] = ARTIST_UPLOAD_FOLDER
app.config["SONG_UPLOAD_FOLDER"] = SONG_UPLOAD_FOLDER
app.config["VOTE_UPLOAD_FOLDER"] = VOTE_UPLOAD_FOLDER
app.config["EVENT_UPLOAD_FOLDER"] = EVENT_UPLOAD_FOLDER
app.config["NEWS_UPLOAD_FOLDER"] = NEWS_UPLOAD_FOLDER

os.makedirs(ARTIST_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SONG_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(VOTE_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EVENT_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(NEWS_UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Add it to your environment or .env file.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def fetchone(query, params=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchone()
    finally:
        conn.close()


def fetchall(query, params=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchall()
    finally:
        conn.close()


def execute_commit(query, params=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
        conn.commit()
    finally:
        conn.close()


def execute_returning_one(query, params=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            row = cur.fetchone()
        conn.commit()
        return row
    finally:
        conn.close()


def get_event_live_status(event):
    now = datetime.now()
    start_dt = None
    end_dt = None

    if event.get("start_datetime"):
        start_dt = datetime.strptime(event["start_datetime"], "%Y-%m-%dT%H:%M")
    if event.get("end_datetime"):
        end_dt = datetime.strptime(event["end_datetime"], "%Y-%m-%dT%H:%M")

    manual_status = event.get("status")

    if manual_status == "Closed":
        return "Closed"

    if start_dt and now < start_dt:
        return "Upcoming"

    if end_dt and now > end_dt:
        return "Ended"

    return "Open"


def get_or_create_device_token():
    device_token = request.cookies.get("vote_device_token")
    if not device_token:
        device_token = secrets.token_hex(24)
    return device_token


def has_device_voted(event_id, device_token):
    voted = fetchone(
        """
        SELECT id
        FROM vote_device_logs
        WHERE event_id = %s AND device_token = %s
        LIMIT 1
        """,
        (event_id, device_token),
    )
    return voted is not None


def get_public_vote_events():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM vote_events
                ORDER BY created_at DESC, id DESC
                LIMIT 2
                """
            )
            events = cur.fetchall()

            public_vote_events = []

            for event in events:
                live_status = get_event_live_status(event)

                if live_status in ["Open", "Upcoming"]:
                    cur.execute(
                        """
                        SELECT *
                        FROM vote_candidates
                        WHERE event_id = %s
                        ORDER BY votes_count DESC, candidate_name ASC
                        LIMIT 4
                        """,
                        (event["id"],),
                    )
                    candidates = cur.fetchall()

                    public_vote_events.append({
                        "event": event,
                        "live_status": live_status,
                        "candidates": candidates
                    })

            return public_vote_events
    finally:
        conn.close()


def init_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artists (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    style TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Active',
                    history TEXT,
                    facebook TEXT,
                    instagram TEXT,
                    tiktok TEXT,
                    youtube_channel TEXT,
                    spotify_channel TEXT,
                    image TEXT,
                    total_views INTEGER DEFAULT 0
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS songs (
                    id SERIAL PRIMARY KEY,
                    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    youtube_views INTEGER DEFAULT 0,
                    spotify_streams INTEGER DEFAULT 0
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chart_songs (
                    id SERIAL PRIMARY KEY,
                    artist_name TEXT NOT NULL,
                    song_title TEXT NOT NULL,
                    rank_number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Published',
                    image TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vote_events (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    category_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Open',
                    vote_rule TEXT NOT NULL DEFAULT 'one_per_device',
                    start_datetime TEXT,
                    end_datetime TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vote_candidates (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES vote_events(id) ON DELETE CASCADE,
                    candidate_name TEXT NOT NULL,
                    image TEXT,
                    votes_count INTEGER DEFAULT 0
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vote_device_logs (
                    id SERIAL PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES vote_events(id) ON DELETE CASCADE,
                    candidate_id INTEGER NOT NULL REFERENCES vote_candidates(id) ON DELETE CASCADE,
                    device_token TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events_list (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    event_datetime TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    ticket_link TEXT,
                    image TEXT,
                    status TEXT NOT NULL DEFAULT 'Published',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    image TEXT,
                    status TEXT NOT NULL DEFAULT 'Draft',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("ALTER TABLE artists ADD COLUMN IF NOT EXISTS image TEXT")
            cursor.execute("ALTER TABLE vote_events ADD COLUMN IF NOT EXISTS start_datetime TEXT")
            cursor.execute("ALTER TABLE vote_events ADD COLUMN IF NOT EXISTS end_datetime TEXT")
            cursor.execute("ALTER TABLE vote_events ADD COLUMN IF NOT EXISTS vote_rule TEXT NOT NULL DEFAULT 'one_per_device'")
            cursor.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS image TEXT")
            cursor.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'Draft'")
            cursor.execute("ALTER TABLE news ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

            admin_email = "admin@maskandihub.com"
            admin_password = "admin123"
            hashed_password = generate_password_hash(admin_password)

            cursor.execute("SELECT * FROM admins WHERE email = %s", (admin_email,))
            existing_admin = cursor.fetchone()

            if not existing_admin:
                cursor.execute(
                    "INSERT INTO admins (email, password) VALUES (%s, %s)",
                    (admin_email, hashed_password)
                )

        conn.commit()
    finally:
        conn.close()


def admin_required():
    if not session.get("admin_logged_in"):
        flash("Please log in first")
        return False
    return True


@app.route("/")
def home():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM artists
                WHERE status = 'Active'
                ORDER BY total_views DESC, id DESC
                LIMIT 3
            """)
            featured_artists = cur.fetchall()

            cur.execute("""
                SELECT *
                FROM chart_songs
                WHERE status = 'Published'
                ORDER BY rank_number ASC
                LIMIT 5
            """)
            top_chart_songs = cur.fetchall()

            cur.execute("""
                SELECT *
                FROM events_list
                WHERE status = 'Published'
                  AND TO_TIMESTAMP(event_datetime, 'YYYY-MM-DD"T"HH24:MI') >= CURRENT_TIMESTAMP
                ORDER BY TO_TIMESTAMP(event_datetime, 'YYYY-MM-DD"T"HH24:MI') ASC
                LIMIT 3
            """)
            upcoming_events = cur.fetchall()

            cur.execute("""
                SELECT *
                FROM news
                WHERE status = 'Published'
                ORDER BY created_at DESC, id DESC
                LIMIT 3
            """)
            latest_news = cur.fetchall()

            cur.execute("SELECT COUNT(*) AS total FROM artists")
            total_artists_row = cur.fetchone()

            cur.execute("SELECT COUNT(*) AS total FROM chart_songs")
            total_songs_row = cur.fetchone()

            cur.execute("SELECT COALESCE(SUM(votes_count), 0) AS total FROM vote_candidates")
            total_votes_row = cur.fetchone()
    finally:
        conn.close()

    public_vote_events = get_public_vote_events()

    return render_template(
        "index.html",
        featured_artists=featured_artists,
        top_chart_songs=top_chart_songs,
        upcoming_events=upcoming_events,
        latest_news=latest_news,
        public_vote_events=public_vote_events,
        total_artists=total_artists_row["total"] if total_artists_row else 0,
        total_songs=total_songs_row["total"] if total_songs_row else 0,
        total_votes=total_votes_row["total"] if total_votes_row else 0
    )


@app.route("/artists")
def artists():
    search = request.args.get("search", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 9

    if page < 1:
        page = 1

    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            count_query = """
                SELECT COUNT(*) AS total
                FROM artists
                WHERE status = 'Active'
            """
            data_query = """
                SELECT *
                FROM artists
                WHERE status = 'Active'
            """
            params = []

            if search:
                count_query += " AND (name ILIKE %s OR style ILIKE %s OR history ILIKE %s)"
                data_query += " AND (name ILIKE %s OR style ILIKE %s OR history ILIKE %s)"
                like_value = f"%{search}%"
                params.extend([like_value, like_value, like_value])

            data_query += " ORDER BY total_views DESC, name ASC LIMIT %s OFFSET %s"

            cur.execute(count_query, params)
            total_row = cur.fetchone()
            total_artists = total_row["total"] if total_row else 0

            cur.execute(data_query, params + [per_page, offset])
            artists_list = cur.fetchall()
    finally:
        conn.close()

    total_pages = math.ceil(total_artists / per_page) if total_artists > 0 else 1
    has_next = page < total_pages
    has_prev = page > 1

    return render_template(
        "artists.html",
        artists_list=artists_list,
        search=search,
        page=page,
        total_pages=total_pages,
        has_next=has_next,
        has_prev=has_prev
    )


@app.route("/top20")
def top20():
    top_songs = fetchall("""
        SELECT *
        FROM chart_songs
        WHERE status = 'Published'
        ORDER BY rank_number ASC
        LIMIT 20
    """)
    return render_template("top20.html", top_songs=top_songs)


@app.route("/vote")
def vote():
    device_token = get_or_create_device_token()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM vote_events
                ORDER BY
                    CASE
                        WHEN status = 'Closed' THEN 3
                        ELSE 0
                    END,
                    start_datetime ASC,
                    created_at DESC
            """)
            events = cur.fetchall()

            vote_events_data = []

            for event in events:
                live_status = get_event_live_status(event)

                if live_status not in ["Open", "Upcoming"]:
                    continue

                cur.execute("""
                    SELECT *
                    FROM vote_candidates
                    WHERE event_id = %s
                    ORDER BY votes_count DESC, candidate_name ASC
                """, (event["id"],))
                candidates = cur.fetchall()

                cur.execute("""
                    SELECT COALESCE(SUM(votes_count), 0) AS total_votes
                    FROM vote_candidates
                    WHERE event_id = %s
                """, (event["id"],))
                total_votes_row = cur.fetchone()

                total_votes = total_votes_row["total_votes"] if total_votes_row else 0
                voted_already = has_device_voted(event["id"], device_token)

                vote_events_data.append({
                    "event": event,
                    "live_status": live_status,
                    "candidates": candidates,
                    "total_votes": total_votes,
                    "voted_already": voted_already
                })
    finally:
        conn.close()

    response = make_response(render_template("vote.html", vote_events=vote_events_data))
    response.set_cookie(
        "vote_device_token",
        device_token,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="Lax"
    )
    return response


@app.route("/vote/submit/<int:event_id>/<int:candidate_id>", methods=["POST"])
def submit_vote(event_id, candidate_id):
    device_token = get_or_create_device_token()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM vote_events
                WHERE id = %s
            """, (event_id,))
            event = cur.fetchone()

            if not event:
                flash("Voting event not found.")
                response = make_response(redirect(url_for("vote")))
                response.set_cookie("vote_device_token", device_token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
                return response

            live_status = get_event_live_status(event)

            if live_status == "Upcoming":
                flash("This voting event is upcoming. Voting has not opened yet.")
                response = make_response(redirect(url_for("vote")))
                response.set_cookie("vote_device_token", device_token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
                return response

            if live_status != "Open":
                flash("This voting event is not open for voting.")
                response = make_response(redirect(url_for("vote")))
                response.set_cookie("vote_device_token", device_token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
                return response

            cur.execute("""
                SELECT id
                FROM vote_device_logs
                WHERE event_id = %s AND device_token = %s
                LIMIT 1
            """, (event_id, device_token))
            existing_vote = cur.fetchone()

            if existing_vote:
                flash("You have already voted on this device for this category.")
                response = make_response(redirect(url_for("vote")))
                response.set_cookie("vote_device_token", device_token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
                return response

            cur.execute("""
                SELECT *
                FROM vote_candidates
                WHERE id = %s AND event_id = %s
            """, (candidate_id, event_id))
            candidate = cur.fetchone()

            if not candidate:
                flash("Vote option not found.")
                response = make_response(redirect(url_for("vote")))
                response.set_cookie("vote_device_token", device_token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
                return response

            cur.execute("""
                UPDATE vote_candidates
                SET votes_count = votes_count + 1
                WHERE id = %s
            """, (candidate_id,))

            cur.execute("""
                INSERT INTO vote_device_logs (event_id, candidate_id, device_token)
                VALUES (%s, %s, %s)
            """, (event_id, candidate_id, device_token))

        conn.commit()
    finally:
        conn.close()

    flash("Your vote has been recorded successfully.")
    response = make_response(redirect(url_for("vote")))
    response.set_cookie(
        "vote_device_token",
        device_token,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="Lax"
    )
    return response


@app.route("/events")
def events():
    public_events = fetchall("""
        SELECT *
        FROM events_list
        WHERE status = 'Published'
          AND TO_TIMESTAMP(event_datetime, 'YYYY-MM-DD"T"HH24:MI') >= CURRENT_TIMESTAMP
        ORDER BY TO_TIMESTAMP(event_datetime, 'YYYY-MM-DD"T"HH24:MI') ASC
    """)
    return render_template("events.html", public_events=public_events)


@app.route("/events/<int:event_id>")
def event_details(event_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM events_list
                WHERE id = %s
                  AND status = 'Published'
                  AND TO_TIMESTAMP(event_datetime, 'YYYY-MM-DD"T"HH24:MI') >= CURRENT_TIMESTAMP
            """, (event_id,))
            event = cur.fetchone()

            cur.execute("""
                SELECT *
                FROM events_list
                WHERE status = 'Published'
                  AND TO_TIMESTAMP(event_datetime, 'YYYY-MM-DD"T"HH24:MI') >= CURRENT_TIMESTAMP
                  AND id != %s
                ORDER BY TO_TIMESTAMP(event_datetime, 'YYYY-MM-DD"T"HH24:MI') ASC
                LIMIT 3
            """, (event_id,))
            upcoming_events = cur.fetchall()
    finally:
        conn.close()

    if not event:
        flash("Event not found or is no longer upcoming.")
        return redirect(url_for("events"))

    return render_template("event_details.html", event=event, upcoming_events=upcoming_events)


@app.route("/news")
def news():
    news_items = fetchall("""
        SELECT *
        FROM news
        WHERE status = 'Published'
        ORDER BY created_at DESC, id DESC
    """)
    return render_template("news.html", news_items=news_items)


@app.route("/news/<int:news_id>")
def news_details(news_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM news
                WHERE id = %s
                  AND status = 'Published'
            """, (news_id,))
            article = cur.fetchone()

            cur.execute("""
                SELECT *
                FROM news
                WHERE status = 'Published'
                  AND id != %s
                ORDER BY created_at DESC, id DESC
                LIMIT 4
            """, (news_id,))
            latest_news = cur.fetchall()
    finally:
        conn.close()

    if not article:
        flash("News article not found.")
        return redirect(url_for("news"))

    return render_template("news_details.html", article=article, latest_news=latest_news)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        admin = fetchone(
            "SELECT * FROM admins WHERE email = %s",
            (email,)
        )

        if admin and check_password_hash(admin["password"], password):
            session["admin_logged_in"] = True
            session["admin_email"] = admin["email"]
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Invalid email or password")
            return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/register")
def register():
    return render_template("register.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/admin")
def admin_dashboard():
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM artists")
            total_artists_row = cur.fetchone()

            cur.execute("SELECT COUNT(*) AS total FROM chart_songs")
            total_songs_row = cur.fetchone()

            cur.execute("SELECT COALESCE(SUM(votes_count), 0) AS total FROM vote_candidates")
            total_votes_row = cur.fetchone()

            cur.execute("""
                SELECT COUNT(*) AS total
                FROM events_list
                WHERE status = 'Published'
                  AND TO_TIMESTAMP(event_datetime, 'YYYY-MM-DD"T"HH24:MI') >= CURRENT_TIMESTAMP
            """)
            upcoming_events_row = cur.fetchone()

            cur.execute("SELECT COUNT(*) AS total FROM news")
            total_news_row = cur.fetchone()

            cur.execute("""
                SELECT id, name, style, status
                FROM artists
                ORDER BY id DESC
                LIMIT 3
            """)
            recent_artists = cur.fetchall()

            cur.execute("""
                SELECT id, name, venue, event_datetime, status
                FROM events_list
                ORDER BY created_at DESC, id DESC
                LIMIT 3
            """)
            recent_events = cur.fetchall()

            cur.execute("""
                SELECT id, title, category, created_at, status
                FROM news
                ORDER BY created_at DESC, id DESC
                LIMIT 3
            """)
            recent_news = cur.fetchall()

            cur.execute("""
                SELECT id, title, category_type, status, start_datetime, end_datetime
                FROM vote_events
                ORDER BY created_at DESC, id DESC
                LIMIT 3
            """)
            recent_votes = cur.fetchall()
    finally:
        conn.close()

    recent_updates = []

    for artist in recent_artists:
        recent_updates.append({
            "title": f"{artist['name']} artist profile",
            "description": f"Style: {artist['style']} • Status: {artist['status']}",
            "type": "artist"
        })

    for event in recent_events:
        recent_updates.append({
            "title": f"{event['name']} event",
            "description": f"Venue: {event['venue']} • Date: {event['event_datetime']}",
            "type": "event"
        })

    for news_item in recent_news:
        recent_updates.append({
            "title": news_item["title"],
            "description": f"News category: {news_item['category']} • Status: {news_item['status']}",
            "type": "news"
        })

    for vote_item in recent_votes:
        recent_updates.append({
            "title": f"{vote_item['title']} voting event",
            "description": f"Category: {vote_item['category_type']} • Status: {vote_item['status']}",
            "type": "vote"
        })

    recent_updates = recent_updates[:6]

    return render_template(
        "admin/admin_dashboard.html",
        total_artists=total_artists_row["total"] if total_artists_row else 0,
        total_songs=total_songs_row["total"] if total_songs_row else 0,
        total_votes=total_votes_row["total"] if total_votes_row else 0,
        upcoming_events=upcoming_events_row["total"] if upcoming_events_row else 0,
        total_news=total_news_row["total"] if total_news_row else 0,
        recent_updates=recent_updates
    )


@app.route("/admin/artists", methods=["GET"])
def manage_artists():
    if not admin_required():
        return redirect(url_for("login"))

    search = request.args.get("search", "").strip()
    style_filter = request.args.get("style", "").strip()
    status_filter = request.args.get("status", "").strip()
    sort_by = request.args.get("sort", "name_asc")

    query = "SELECT * FROM artists WHERE 1=1"
    params = []

    if search:
        query += " AND (name ILIKE %s OR history ILIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])

    if style_filter:
        query += " AND style = %s"
        params.append(style_filter)

    if status_filter:
        query += " AND status = %s"
        params.append(status_filter)

    if sort_by == "name_desc":
        query += " ORDER BY name DESC"
    elif sort_by == "views_desc":
        query += " ORDER BY total_views DESC"
    elif sort_by == "views_asc":
        query += " ORDER BY total_views ASC"
    else:
        query += " ORDER BY name ASC"

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            artists = cur.fetchall()

            artist_list = []
            for artist in artists:
                cur.execute("""
                    SELECT *
                    FROM songs
                    WHERE artist_id = %s
                    ORDER BY youtube_views DESC, spotify_streams DESC
                    LIMIT 5
                """, (artist["id"],))
                songs = cur.fetchall()

                artist_list.append({
                    "artist": artist,
                    "songs": songs
                })

            cur.execute("SELECT DISTINCT style FROM artists ORDER BY style ASC")
            styles = cur.fetchall()
    finally:
        conn.close()

    return render_template(
        "admin/manage_artists.html",
        artist_list=artist_list,
        styles=styles,
        search=search,
        style_filter=style_filter,
        status_filter=status_filter,
        sort_by=sort_by
    )


@app.route("/admin/artists/add", methods=["POST"])
def add_artist():
    if not admin_required():
        return redirect(url_for("login"))

    name = request.form["name"]
    style = request.form["style"]
    status = request.form["status"]
    history = request.form["history"]
    facebook = request.form["facebook"]
    instagram = request.form["instagram"]
    tiktok = request.form["tiktok"]
    youtube_channel = request.form["youtube_channel"]
    spotify_channel = request.form["spotify_channel"]

    image_name = None
    image_file = request.files.get("image")
    if image_file and image_file.filename:
        if allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            image_name = f"{name.lower().replace(' ', '_')}_{filename}"
            image_file.save(os.path.join(app.config["ARTIST_UPLOAD_FOLDER"], image_name))
        else:
            flash("Invalid image format. Use png, jpg, jpeg, or webp.")
            return redirect(url_for("manage_artists"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO artists
                (name, style, status, history, facebook, instagram, tiktok, youtube_channel, spotify_channel, image)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                name, style, status, history, facebook, instagram, tiktok, youtube_channel, spotify_channel, image_name
            ))

            artist_id = cursor.fetchone()["id"]
            total_views = 0

            for i in range(1, 6):
                song_title = request.form.get(f"song_title_{i}", "").strip()
                youtube_views = request.form.get(f"youtube_views_{i}", "0").strip()
                spotify_streams = request.form.get(f"spotify_streams_{i}", "0").strip()

                if song_title:
                    yv = int(youtube_views) if youtube_views.isdigit() else 0
                    sp = int(spotify_streams) if spotify_streams.isdigit() else 0
                    total_views += yv + sp

                    cursor.execute("""
                        INSERT INTO songs (artist_id, title, youtube_views, spotify_streams)
                        VALUES (%s, %s, %s, %s)
                    """, (artist_id, song_title, yv, sp))

            cursor.execute("UPDATE artists SET total_views = %s WHERE id = %s", (total_views, artist_id))

        conn.commit()
    finally:
        conn.close()

    flash("Artist added successfully")
    return redirect(url_for("manage_artists"))


@app.route("/admin/artists/delete/<int:artist_id>", methods=["POST"])
def delete_artist(artist_id):
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM songs WHERE artist_id = %s", (artist_id,))
            cur.execute("DELETE FROM artists WHERE id = %s", (artist_id,))
        conn.commit()
    finally:
        conn.close()

    flash("Artist deleted successfully")
    return redirect(url_for("manage_artists"))


@app.route("/admin/artists/edit/<int:artist_id>", methods=["GET", "POST"])
def edit_artist(artist_id):
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if request.method == "POST":
                name = request.form["name"]
                style = request.form["style"]
                status = request.form["status"]
                history = request.form["history"]
                facebook = request.form["facebook"]
                instagram = request.form["instagram"]
                tiktok = request.form["tiktok"]
                youtube_channel = request.form["youtube_channel"]
                spotify_channel = request.form["spotify_channel"]

                cur.execute("SELECT * FROM artists WHERE id = %s", (artist_id,))
                artist = cur.fetchone()
                image_name = artist["image"]

                image_file = request.files.get("image")
                if image_file and image_file.filename:
                    if allowed_file(image_file.filename):
                        filename = secure_filename(image_file.filename)
                        image_name = f"{name.lower().replace(' ', '_')}_{filename}"
                        image_file.save(os.path.join(app.config["ARTIST_UPLOAD_FOLDER"], image_name))
                    else:
                        flash("Invalid image format. Use png, jpg, jpeg, or webp.")
                        return redirect(url_for("edit_artist", artist_id=artist_id))

                cur.execute("""
                    UPDATE artists
                    SET name = %s, style = %s, status = %s, history = %s, facebook = %s, instagram = %s,
                        tiktok = %s, youtube_channel = %s, spotify_channel = %s, image = %s
                    WHERE id = %s
                """, (
                    name, style, status, history, facebook, instagram, tiktok,
                    youtube_channel, spotify_channel, image_name, artist_id
                ))

                cur.execute("DELETE FROM songs WHERE artist_id = %s", (artist_id,))
                total_views = 0

                for i in range(1, 6):
                    song_title = request.form.get(f"song_title_{i}", "").strip()
                    youtube_views = request.form.get(f"youtube_views_{i}", "0").strip()
                    spotify_streams = request.form.get(f"spotify_streams_{i}", "0").strip()

                    if song_title:
                        yv = int(youtube_views) if youtube_views.isdigit() else 0
                        sp = int(spotify_streams) if spotify_streams.isdigit() else 0
                        total_views += yv + sp

                        cur.execute("""
                            INSERT INTO songs (artist_id, title, youtube_views, spotify_streams)
                            VALUES (%s, %s, %s, %s)
                        """, (artist_id, song_title, yv, sp))

                cur.execute("UPDATE artists SET total_views = %s WHERE id = %s", (total_views, artist_id))
                conn.commit()

                flash("Artist updated successfully")
                return redirect(url_for("manage_artists"))

            cur.execute("SELECT * FROM artists WHERE id = %s", (artist_id,))
            artist = cur.fetchone()

            cur.execute("""
                SELECT *
                FROM songs
                WHERE artist_id = %s
                ORDER BY id ASC
                LIMIT 5
            """, (artist_id,))
            songs = cur.fetchall()
    finally:
        conn.close()

    return render_template("admin/edit_artist.html", artist=artist, songs=songs)


@app.route("/admin/songs")
def manage_songs():
    if not admin_required():
        return redirect(url_for("login"))

    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()

    query = "SELECT * FROM chart_songs WHERE 1=1"
    params = []

    if search:
        query += " AND (song_title ILIKE %s OR artist_name ILIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])

    if status_filter:
        query += " AND status = %s"
        params.append(status_filter)

    query += " ORDER BY rank_number ASC"

    chart_songs = fetchall(query, params)

    return render_template(
        "admin/manage_songs.html",
        chart_songs=chart_songs,
        search=search,
        status_filter=status_filter
    )


@app.route("/admin/songs/add", methods=["POST"])
def add_chart_song():
    if not admin_required():
        return redirect(url_for("login"))

    artist_name = request.form["artist_name"].strip()
    song_title = request.form["song_title"].strip()
    rank_number = request.form["rank_number"].strip()
    status = request.form["status"].strip()

    if not artist_name or not song_title or not rank_number:
        flash("Artist name, song name, and rank are required.")
        return redirect(url_for("manage_songs"))

    try:
        rank_number = int(rank_number)
    except ValueError:
        flash("Rank must be a valid number.")
        return redirect(url_for("manage_songs"))

    image_name = None
    image_file = request.files.get("image")
    if image_file and image_file.filename:
        if allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            image_name = f"{artist_name.lower().replace(' ', '_')}_{song_title.lower().replace(' ', '_')}_{filename}"
            image_file.save(os.path.join(app.config["SONG_UPLOAD_FOLDER"], image_name))
        else:
            flash("Invalid song image format. Use png, jpg, jpeg, or webp.")
            return redirect(url_for("manage_songs"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE chart_songs
                SET rank_number = rank_number + 1
                WHERE rank_number >= %s
            """, (rank_number,))

            cur.execute("""
                INSERT INTO chart_songs (artist_name, song_title, rank_number, status, image)
                VALUES (%s, %s, %s, %s, %s)
            """, (artist_name, song_title, rank_number, status, image_name))

        conn.commit()
    finally:
        conn.close()

    flash("Chart song added successfully.")
    return redirect(url_for("manage_songs"))


@app.route("/admin/songs/delete/<int:song_id>", methods=["POST"])
def delete_chart_song(song_id):
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM chart_songs WHERE id = %s", (song_id,))
            song = cur.fetchone()

            if song:
                deleted_rank = song["rank_number"]
                cur.execute("DELETE FROM chart_songs WHERE id = %s", (song_id,))
                cur.execute("""
                    UPDATE chart_songs
                    SET rank_number = rank_number - 1
                    WHERE rank_number > %s
                """, (deleted_rank,))
                conn.commit()
    finally:
        conn.close()

    flash("Chart song deleted successfully.")
    return redirect(url_for("manage_songs"))


@app.route("/admin/songs/edit/<int:song_id>", methods=["GET", "POST"])
def edit_chart_song(song_id):
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM chart_songs WHERE id = %s", (song_id,))
            song = cur.fetchone()

            if not song:
                flash("Song not found.")
                return redirect(url_for("manage_songs"))

            if request.method == "POST":
                artist_name = request.form["artist_name"].strip()
                song_title = request.form["song_title"].strip()
                new_rank = int(request.form["rank_number"])
                status = request.form["status"].strip()
                image_name = song["image"]
                old_rank = song["rank_number"]

                image_file = request.files.get("image")
                if image_file and image_file.filename:
                    if allowed_file(image_file.filename):
                        filename = secure_filename(image_file.filename)
                        image_name = f"{artist_name.lower().replace(' ', '_')}_{song_title.lower().replace(' ', '_')}_{filename}"
                        image_file.save(os.path.join(app.config["SONG_UPLOAD_FOLDER"], image_name))
                    else:
                        flash("Invalid song image format.")
                        return redirect(url_for("edit_chart_song", song_id=song_id))

                if new_rank != old_rank:
                    if new_rank < old_rank:
                        cur.execute("""
                            UPDATE chart_songs
                            SET rank_number = rank_number + 1
                            WHERE rank_number >= %s AND rank_number < %s AND id != %s
                        """, (new_rank, old_rank, song_id))
                    else:
                        cur.execute("""
                            UPDATE chart_songs
                            SET rank_number = rank_number - 1
                            WHERE rank_number <= %s AND rank_number > %s AND id != %s
                        """, (new_rank, old_rank, song_id))

                cur.execute("""
                    UPDATE chart_songs
                    SET artist_name = %s, song_title = %s, rank_number = %s, status = %s, image = %s
                    WHERE id = %s
                """, (artist_name, song_title, new_rank, status, image_name, song_id))

                conn.commit()
                flash("Chart song updated successfully.")
                return redirect(url_for("manage_songs"))
    finally:
        conn.close()

    return render_template("admin/edit_chart_song.html", song=song)


@app.route("/admin/songs/move-up/<int:song_id>", methods=["POST"])
def move_chart_song_up(song_id):
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM chart_songs WHERE id = %s", (song_id,))
            song = cur.fetchone()

            if song and song["rank_number"] > 1:
                current_rank = song["rank_number"]
                cur.execute("SELECT * FROM chart_songs WHERE rank_number = %s", (current_rank - 1,))
                above_song = cur.fetchone()

                if above_song:
                    cur.execute("UPDATE chart_songs SET rank_number = %s WHERE id = %s", (current_rank, above_song["id"]))
                    cur.execute("UPDATE chart_songs SET rank_number = %s WHERE id = %s", (current_rank - 1, song_id))
                    conn.commit()
    finally:
        conn.close()

    return redirect(url_for("manage_songs"))


@app.route("/admin/songs/move-down/<int:song_id>", methods=["POST"])
def move_chart_song_down(song_id):
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM chart_songs WHERE id = %s", (song_id,))
            song = cur.fetchone()

            cur.execute("SELECT MAX(rank_number) AS max_rank FROM chart_songs")
            max_rank_row = cur.fetchone()
            max_rank = max_rank_row["max_rank"] if max_rank_row and max_rank_row["max_rank"] else 0

            if song and song["rank_number"] < max_rank:
                current_rank = song["rank_number"]
                cur.execute("SELECT * FROM chart_songs WHERE rank_number = %s", (current_rank + 1,))
                below_song = cur.fetchone()

                if below_song:
                    cur.execute("UPDATE chart_songs SET rank_number = %s WHERE id = %s", (current_rank, below_song["id"]))
                    cur.execute("UPDATE chart_songs SET rank_number = %s WHERE id = %s", (current_rank + 1, song_id))
                    conn.commit()
    finally:
        conn.close()

    return redirect(url_for("manage_songs"))


@app.route("/admin/votes")
def manage_votes():
    if not admin_required():
        return redirect(url_for("login"))

    search = request.args.get("search", "").strip()
    type_filter = request.args.get("type", "").strip()
    state_filter = request.args.get("state", "").strip()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM vote_events ORDER BY created_at DESC, id DESC")
            events = cur.fetchall()

            filtered_events = []
            summary_counts = {
                "Open": 0,
                "Closed": 0,
                "Upcoming": 0,
                "Ended": 0
            }

            for event in events:
                live_status = get_event_live_status(event)
                summary_counts[live_status] = summary_counts.get(live_status, 0) + 1

                if search and search.lower() not in event["title"].lower():
                    continue
                if type_filter and event["category_type"] != type_filter:
                    continue
                if state_filter and live_status != state_filter:
                    continue

                cur.execute("""
                    SELECT *
                    FROM vote_candidates
                    WHERE event_id = %s
                    ORDER BY votes_count DESC, candidate_name ASC
                """, (event["id"],))
                candidates = cur.fetchall()

                cur.execute("""
                    SELECT COALESCE(SUM(votes_count), 0) AS total_votes
                    FROM vote_candidates
                    WHERE event_id = %s
                """, (event["id"],))
                total_votes_row = cur.fetchone()
                total_votes = total_votes_row["total_votes"] if total_votes_row else 0
                leader = candidates[0] if candidates else None

                candidate_list = []
                for candidate in candidates:
                    progress = 0
                    if total_votes > 0:
                        progress = round((candidate["votes_count"] / total_votes) * 100, 1)

                    candidate_list.append({
                        "candidate": candidate,
                        "progress": progress
                    })

                filtered_events.append({
                    "event": event,
                    "live_status": live_status,
                    "candidates": candidate_list,
                    "leader": leader,
                    "total_votes": total_votes
                })
    finally:
        conn.close()

    return render_template(
        "admin/manage_votes.html",
        event_list=filtered_events,
        search=search,
        type_filter=type_filter,
        state_filter=state_filter,
        summary_counts=summary_counts
    )


@app.route("/admin/votes/add-event", methods=["POST"])
def add_vote_event():
    if not admin_required():
        return redirect(url_for("login"))

    title = request.form["title"].strip()
    category_type = request.form["category_type"].strip()
    manual_status = request.form["status"].strip()
    start_datetime = request.form["start_datetime"].strip()
    end_datetime = request.form["end_datetime"].strip()

    if not title or not category_type or not start_datetime or not end_datetime:
        flash("Title, category, start date, and end date are required.")
        return redirect(url_for("manage_votes"))

    try:
        start_obj = datetime.strptime(start_datetime, "%Y-%m-%dT%H:%M")
        end_obj = datetime.strptime(end_datetime, "%Y-%m-%dT%H:%M")
    except ValueError:
        flash("Invalid date format.")
        return redirect(url_for("manage_votes"))

    if end_obj <= start_obj:
        flash("Close date/time must be after start date/time.")
        return redirect(url_for("manage_votes"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO vote_events (title, category_type, status, vote_rule, start_datetime, end_datetime)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (title, category_type, manual_status, "one_per_device", start_datetime, end_datetime))

            event_id = cursor.fetchone()["id"]

            for i in range(1, 6):
                candidate_name = request.form.get(f"candidate_name_{i}", "").strip()
                image_file = request.files.get(f"candidate_image_{i}")
                image_name = None

                if candidate_name:
                    if image_file and image_file.filename:
                        if allowed_file(image_file.filename):
                            filename = secure_filename(image_file.filename)
                            image_name = f"{candidate_name.lower().replace(' ', '_')}_{filename}"
                            image_file.save(os.path.join(app.config["VOTE_UPLOAD_FOLDER"], image_name))
                        else:
                            flash("Invalid candidate image format.")
                            return redirect(url_for("manage_votes"))

                    cursor.execute("""
                        INSERT INTO vote_candidates (event_id, candidate_name, image, votes_count)
                        VALUES (%s, %s, %s, 0)
                    """, (event_id, candidate_name, image_name))

        conn.commit()
    finally:
        conn.close()

    flash("Voting event created successfully.")
    return redirect(url_for("manage_votes"))


@app.route("/admin/votes/add-candidate/<int:event_id>", methods=["POST"])
def add_vote_candidate(event_id):
    if not admin_required():
        return redirect(url_for("login"))

    candidate_name = request.form["candidate_name"].strip()

    if not candidate_name:
        flash("Candidate name is required.")
        return redirect(url_for("manage_votes"))

    image_name = None
    image_file = request.files.get("image")

    if image_file and image_file.filename:
        if allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            image_name = f"{candidate_name.lower().replace(' ', '_')}_{filename}"
            image_file.save(os.path.join(app.config["VOTE_UPLOAD_FOLDER"], image_name))
        else:
            flash("Invalid candidate image format.")
            return redirect(url_for("manage_votes"))

    execute_commit("""
        INSERT INTO vote_candidates (event_id, candidate_name, image, votes_count)
        VALUES (%s, %s, %s, 0)
    """, (event_id, candidate_name, image_name))

    flash("Candidate added successfully.")
    return redirect(url_for("manage_votes"))


@app.route("/admin/votes/toggle-status/<int:event_id>", methods=["POST"])
def toggle_vote_event_status(event_id):
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM vote_events WHERE id = %s", (event_id,))
            event = cur.fetchone()

            if event:
                new_status = "Closed" if event["status"] == "Open" else "Open"
                cur.execute("UPDATE vote_events SET status = %s WHERE id = %s", (new_status, event_id))
                conn.commit()
    finally:
        conn.close()

    flash("Voting event status updated.")
    return redirect(url_for("manage_votes"))


@app.route("/admin/votes/delete-candidate/<int:candidate_id>", methods=["POST"])
def delete_vote_candidate(candidate_id):
    if not admin_required():
        return redirect(url_for("login"))

    execute_commit("DELETE FROM vote_candidates WHERE id = %s", (candidate_id,))
    flash("Candidate removed successfully.")
    return redirect(url_for("manage_votes"))


@app.route("/admin/votes/delete-event/<int:event_id>", methods=["POST"])
def delete_vote_event(event_id):
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM vote_candidates WHERE event_id = %s", (event_id,))
            cur.execute("DELETE FROM vote_events WHERE id = %s", (event_id,))
        conn.commit()
    finally:
        conn.close()

    flash("Voting event deleted successfully.")
    return redirect(url_for("manage_votes"))


@app.route("/admin/events")
def manage_events():
    if not admin_required():
        return redirect(url_for("login"))

    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()

    query = "SELECT * FROM events_list WHERE 1=1"
    params = []

    if search:
        query += " AND (name ILIKE %s OR venue ILIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])

    if status_filter:
        query += " AND status = %s"
        params.append(status_filter)

    query += " ORDER BY TO_TIMESTAMP(event_datetime, 'YYYY-MM-DD\"T\"HH24:MI') ASC"

    all_events = fetchall(query, params)

    return render_template(
        "admin/manage_events.html",
        events_list=all_events,
        search=search,
        status_filter=status_filter
    )


@app.route("/admin/events/add", methods=["POST"])
def add_event():
    if not admin_required():
        return redirect(url_for("login"))

    name = request.form["name"].strip()
    event_datetime = request.form["event_datetime"].strip()
    venue = request.form["venue"].strip()
    ticket_link = request.form["ticket_link"].strip()
    status = request.form["status"].strip()

    if not name or not event_datetime or not venue:
        flash("Event name, date, and venue are required.")
        return redirect(url_for("manage_events"))

    image_name = None
    image_file = request.files.get("image")
    if image_file and image_file.filename:
        if allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            image_name = f"{name.lower().replace(' ', '_')}_{filename}"
            image_file.save(os.path.join(app.config["EVENT_UPLOAD_FOLDER"], image_name))
        else:
            flash("Invalid event image format.")
            return redirect(url_for("manage_events"))

    execute_commit("""
        INSERT INTO events_list (name, event_datetime, venue, ticket_link, image, status)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (name, event_datetime, venue, ticket_link, image_name, status))

    flash("Event added successfully.")
    return redirect(url_for("manage_events"))


@app.route("/admin/events/edit/<int:event_id>", methods=["GET", "POST"])
def edit_event(event_id):
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM events_list WHERE id = %s", (event_id,))
            event = cur.fetchone()

            if not event:
                flash("Event not found.")
                return redirect(url_for("manage_events"))

            if request.method == "POST":
                name = request.form["name"].strip()
                event_datetime = request.form["event_datetime"].strip()
                venue = request.form["venue"].strip()
                ticket_link = request.form["ticket_link"].strip()
                status = request.form["status"].strip()

                image_name = event["image"]
                image_file = request.files.get("image")

                if image_file and image_file.filename:
                    if allowed_file(image_file.filename):
                        filename = secure_filename(image_file.filename)
                        image_name = f"{name.lower().replace(' ', '_')}_{filename}"
                        image_file.save(os.path.join(app.config["EVENT_UPLOAD_FOLDER"], image_name))
                    else:
                        flash("Invalid event image format.")
                        return redirect(url_for("edit_event", event_id=event_id))

                cur.execute("""
                    UPDATE events_list
                    SET name = %s, event_datetime = %s, venue = %s, ticket_link = %s, image = %s, status = %s
                    WHERE id = %s
                """, (name, event_datetime, venue, ticket_link, image_name, status, event_id))

                conn.commit()
                flash("Event updated successfully")
                return redirect(url_for("manage_events"))
    finally:
        conn.close()

    return render_template("admin/edit_event.html", event=event)


@app.route("/admin/events/delete/<int:event_id>", methods=["POST"])
def delete_event(event_id):
    if not admin_required():
        return redirect(url_for("login"))

    execute_commit("DELETE FROM events_list WHERE id = %s", (event_id,))
    flash("Event deleted successfully.")
    return redirect(url_for("manage_events"))


@app.route("/admin/news")
def manage_news():
    if not admin_required():
        return redirect(url_for("login"))

    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()
    category_filter = request.args.get("category", "").strip()

    query = "SELECT * FROM news WHERE 1=1"
    params = []

    if search:
        query += " AND (title ILIKE %s OR content ILIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])

    if status_filter:
        query += " AND status = %s"
        params.append(status_filter)

    if category_filter:
        query += " AND category = %s"
        params.append(category_filter)

    query += " ORDER BY created_at DESC, id DESC"

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            news_items = cur.fetchall()

            cur.execute("SELECT DISTINCT category FROM news ORDER BY category ASC")
            categories = cur.fetchall()
    finally:
        conn.close()

    return render_template(
        "admin/manage_news.html",
        news_items=news_items,
        categories=categories,
        search=search,
        status_filter=status_filter,
        category_filter=category_filter
    )


@app.route("/admin/news/add", methods=["POST"])
def add_news():
    if not admin_required():
        return redirect(url_for("login"))

    title = request.form["title"].strip()
    category = request.form["category"].strip()
    content = request.form["content"].strip()
    status = request.form["status"].strip()

    if not title or not category or not content:
        flash("Title, category, and content are required.")
        return redirect(url_for("manage_news"))

    image_name = None
    image_file = request.files.get("image")

    if image_file and image_file.filename:
        if allowed_file(image_file.filename):
            filename = secure_filename(image_file.filename)
            image_name = f"{title.lower().replace(' ', '_')}_{filename}"
            image_file.save(os.path.join(app.config["NEWS_UPLOAD_FOLDER"], image_name))
        else:
            flash("Invalid image format. Use png, jpg, jpeg, or webp.")
            return redirect(url_for("manage_news"))

    execute_commit("""
        INSERT INTO news (title, category, content, image, status)
        VALUES (%s, %s, %s, %s, %s)
    """, (title, category, content, image_name, status))

    flash("News added successfully.")
    return redirect(url_for("manage_news"))


@app.route("/admin/news/edit/<int:news_id>", methods=["GET", "POST"])
def edit_news(news_id):
    if not admin_required():
        return redirect(url_for("login"))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM news WHERE id = %s", (news_id,))
            news_item = cur.fetchone()

            if not news_item:
                flash("News item not found.")
                return redirect(url_for("manage_news"))

            if request.method == "POST":
                title = request.form["title"].strip()
                category = request.form["category"].strip()
                content = request.form["content"].strip()
                status = request.form["status"].strip()

                if not title or not category or not content:
                    flash("Title, category, and content are required.")
                    return redirect(url_for("edit_news", news_id=news_id))

                image_name = news_item["image"]
                image_file = request.files.get("image")

                if image_file and image_file.filename:
                    if allowed_file(image_file.filename):
                        filename = secure_filename(image_file.filename)
                        image_name = f"{title.lower().replace(' ', '_')}_{filename}"
                        image_file.save(os.path.join(app.config["NEWS_UPLOAD_FOLDER"], image_name))
                    else:
                        flash("Invalid image format. Use png, jpg, jpeg, or webp.")
                        return redirect(url_for("edit_news", news_id=news_id))

                cur.execute("""
                    UPDATE news
                    SET title = %s, category = %s, content = %s, image = %s, status = %s
                    WHERE id = %s
                """, (title, category, content, image_name, status, news_id))

                conn.commit()
                flash("News updated successfully.")
                return redirect(url_for("manage_news"))
    finally:
        conn.close()

    return render_template("admin/edit_news.html", news_item=news_item)


@app.route("/admin/news/delete/<int:news_id>", methods=["POST"])
def delete_news(news_id):
    if not admin_required():
        return redirect(url_for("login"))

    execute_commit("DELETE FROM news WHERE id = %s", (news_id,))
    flash("News deleted successfully.")
    return redirect(url_for("manage_news"))


@app.route("/artist/<int:artist_id>")
def artist_profile(artist_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM artists WHERE id = %s", (artist_id,))
            artist = cur.fetchone()

            cur.execute("""
                SELECT *
                FROM songs
                WHERE artist_id = %s
                ORDER BY youtube_views DESC, spotify_streams DESC
            """, (artist_id,))
            songs = cur.fetchall()
    finally:
        conn.close()

    if not artist:
        flash("Artist not found")
        return redirect(url_for("artists"))

    return render_template("artist_profile.html", artist=artist, songs=songs)


if __name__ == "__main__":
    init_db()
    app.run(debug=app.config["DEBUG"])