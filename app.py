import os
import json
import re
import uuid
import hashlib
import mysql.connector
from mysql.connector import Error, IntegrityError
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from groq import Groq
import requests

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Optional live travel pricing provider. The app works without these values,
# but then transport/hotel prices are clearly marked as estimates.
AMADEUS_CLIENT_ID = os.getenv("AMADEUS_CLIENT_ID", "")
AMADEUS_CLIENT_SECRET = os.getenv("AMADEUS_CLIENT_SECRET", "")
AMADEUS_BASE_URL = os.getenv("AMADEUS_BASE_URL", "https://test.api.amadeus.com").rstrip("/")
LIVE_PRICING_ENABLED = bool(AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "travel_planner")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@travelplanner.com").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

groq_client = None
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)


# -------------------------------------------------------------------
# DATABASE
# -------------------------------------------------------------------

class DBConnection:
    """Small wrapper so the rest of the app can use connection.execute()."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=None):
        cursor = self.connection.cursor(dictionary=True, buffered=True)
        cursor.execute(sql, params or ())
        return cursor

    def executemany(self, sql, params):
        cursor = self.connection.cursor(dictionary=True, buffered=True)
        cursor.executemany(sql, params)
        return cursor

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def get_server_connection():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
    )


def get_db():
    connection = mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
    )
    return DBConnection(connection)


def init_db():
    server = None
    try:
        server = get_server_connection()
        cursor = server.cursor(buffered=True)
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        server.commit()
        cursor.close()
        server.close()

        connection = get_db()
        cursor = connection.connection.cursor(buffered=True)

        statements = [
            """CREATE TABLE IF NOT EXISTS users (
                id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'user',
                created_at DATETIME NOT NULL
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS trips (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL,
                source VARCHAR(255) NOT NULL,
                destination VARCHAR(255) NOT NULL,
                days INT NOT NULL,
                travel_date DATE NOT NULL,
                travelers INT NOT NULL,
                travel_style VARCHAR(30) NOT NULL,
                interests TEXT,
                budget DECIMAL(14,2) DEFAULT 0,
                result_json LONGTEXT NOT NULL,
                created_at DATETIME NOT NULL,
                CONSTRAINT fk_trips_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                INDEX idx_trips_user (user_id)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS planner_searches (
                search_key VARCHAR(64) PRIMARY KEY,
                source VARCHAR(255) NOT NULL,
                destination VARCHAR(255) NOT NULL,
                travel_date DATE NOT NULL,
                days INT NOT NULL,
                travelers INT NOT NULL,
                travel_style VARCHAR(30) NOT NULL,
                interests TEXT NOT NULL,
                budget DECIMAL(14,2) DEFAULT 0,
                result_json LONGTEXT NOT NULL,
                created_at DATETIME NOT NULL,
                INDEX idx_planner_route_date (source, destination, travel_date),
                INDEX idx_planner_inputs (source, destination, travel_date, days, travelers, travel_style)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS ticket_searches (
                search_key VARCHAR(64) PRIMARY KEY,
                source VARCHAR(255) NOT NULL,
                destination VARCHAR(255) NOT NULL,
                travel_date DATE NOT NULL,
                vehicle_type VARCHAR(30) NOT NULL,
                created_at DATETIME NOT NULL,
                INDEX idx_ticket_search_route (source, destination, travel_date, vehicle_type)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS ticket_results (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NULL,
                search_key VARCHAR(64) NOT NULL,
                source VARCHAR(255) NOT NULL,
                destination VARCHAR(255) NOT NULL,
                travel_date DATE NOT NULL,
                vehicle_type VARCHAR(30) NOT NULL,
                ticket_json LONGTEXT NOT NULL,
                created_at DATETIME NOT NULL,
                CONSTRAINT fk_ticket_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
                CONSTRAINT fk_ticket_search FOREIGN KEY (search_key) REFERENCES ticket_searches(search_key) ON DELETE CASCADE,
                INDEX idx_ticket_search_key (search_key),
                INDEX idx_ticket_route_date (source, destination, travel_date, vehicle_type)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS bookings (
                id VARCHAR(20) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL,
                ticket_result_id VARCHAR(36) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'Booked',
                created_at DATETIME NOT NULL,
                cancelled_at DATETIME NULL,
                CONSTRAINT fk_booking_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                CONSTRAINT fk_booking_ticket FOREIGN KEY (ticket_result_id) REFERENCES ticket_results(id) ON DELETE RESTRICT,
                INDEX idx_bookings_user (user_id)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS passengers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                booking_id VARCHAR(20) NOT NULL,
                name VARCHAR(120) NOT NULL,
                seat VARCHAR(30) NOT NULL,
                CONSTRAINT fk_passenger_booking FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
                UNIQUE KEY uq_booking_seat (booking_id, seat),
                INDEX idx_passengers_booking (booking_id)
            ) ENGINE=InnoDB""",
            """CREATE TABLE IF NOT EXISTS booked_seats (
                id INT AUTO_INCREMENT PRIMARY KEY,
                ticket_result_id VARCHAR(36) NOT NULL,
                booking_id VARCHAR(20) NOT NULL,
                seat VARCHAR(30) NOT NULL,
                created_at DATETIME NOT NULL,
                CONSTRAINT fk_booked_seat_ticket FOREIGN KEY (ticket_result_id) REFERENCES ticket_results(id) ON DELETE CASCADE,
                CONSTRAINT fk_booked_seat_booking FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
                UNIQUE KEY uq_ticket_seat (ticket_result_id, seat),
                UNIQUE KEY uq_booking_seat_global (booking_id, seat),
                INDEX idx_booked_seat_ticket (ticket_result_id)
            ) ENGINE=InnoDB""",
        ]
        for statement in statements:
            cursor.execute(statement)

        # Upgrade existing installations created before travel_date was added.
        cursor.execute(
            """SELECT COUNT(*) AS count
               FROM information_schema.columns
               WHERE table_schema = %s
                 AND table_name = 'trips'
                 AND column_name = 'travel_date'""",
            (MYSQL_DATABASE,),
        )
        has_travel_date = cursor.fetchone()[0] > 0
        if not has_travel_date:
            cursor.execute(
                "ALTER TABLE trips ADD COLUMN travel_date DATE NULL AFTER days"
            )
            cursor.execute(
                "UPDATE trips SET travel_date = CURDATE() WHERE travel_date IS NULL"
            )
            cursor.execute(
                "ALTER TABLE trips MODIFY travel_date DATE NOT NULL"
            )

        # Upgrade existing ticket tables to shared, route/date/type-based inventory.
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_name='ticket_searches'",
            (MYSQL_DATABASE,),
        )
        # ticket_searches is created above; migrate legacy ticket_results columns.
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=%s AND table_name='ticket_results' AND column_name='search_key'",
            (MYSQL_DATABASE,),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute("ALTER TABLE ticket_results ADD COLUMN search_key VARCHAR(64) NULL AFTER user_id")
            cursor.execute("ALTER TABLE ticket_results ADD INDEX idx_ticket_search_key_legacy (search_key)")
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=%s AND table_name='ticket_results' AND column_name='user_id' AND is_nullable='YES'",
            (MYSQL_DATABASE,),
        )
        if cursor.fetchone()[0] == 0:
            try:
                cursor.execute("ALTER TABLE ticket_results DROP FOREIGN KEY fk_ticket_user")
            except Exception:
                pass
            cursor.execute("ALTER TABLE ticket_results MODIFY user_id VARCHAR(36) NULL")
            cursor.execute("ALTER TABLE ticket_results ADD CONSTRAINT fk_ticket_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL")

        admin = connection.execute(
            "SELECT id FROM users WHERE email = %s",
            (ADMIN_EMAIL,),
        ).fetchone()

        if not admin:
            connection.execute(
                """INSERT INTO users (id, name, email, password, role, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    "admin-1",
                    "Travel Planner Admin",
                    ADMIN_EMAIL,
                    generate_password_hash(ADMIN_PASSWORD),
                    "admin",
                    now(),
                ),
            )

        connection.commit()
        cursor.close()
        connection.close()
    except Exception as error:
        if server and server.is_connected():
            server.close()
        raise RuntimeError(
            f"MySQL connection/setup failed: {error}. "
            "Check MySQL service and MYSQL_* values in .env."
        ) from error


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_search_value(value):
    return " ".join(str(value or "").strip().lower().split())


def ticket_search_key(source, destination, travel_date, vehicle_type):
    raw = "|".join([
        normalize_search_value(source),
        normalize_search_value(destination),
        str(travel_date),
        normalize_search_value(vehicle_type or "All"),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def planner_search_key(source, destination, travel_date, days, travelers, travel_style, interests):
    """Stable key for an exact planner search shared across all users."""
    raw = "|".join([
        normalize_search_value(source),
        normalize_search_value(destination),
        str(travel_date).strip(),
        str(int(days)),
        str(int(travelers)),
        normalize_search_value(travel_style),
        normalize_search_value(interests),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def row_to_dict(row):
    return dict(row) if row else None


# Create database and tables when the application starts.
init_db()


# -------------------------------------------------------------------
# AUTH HELPERS
# -------------------------------------------------------------------

def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login to continue.", "warning")
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login to continue.", "warning")
            return redirect(url_for("login"))

        if session.get("role") != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))

        return function(*args, **kwargs)
    return wrapper


@app.context_processor
def template_context():
    return {
        "logged_in": "user_id" in session,
        "current_user": session.get("user_name"),
        "current_role": session.get("role"),
    }


# -------------------------------------------------------------------
# DATA HELPERS
# -------------------------------------------------------------------

def user_from_row(row):
    if not row:
        return None
    return row_to_dict(row)


def trip_from_row(row):
    trip = row_to_dict(row)
    if not trip:
        return None
    trip["result"] = json.loads(trip.pop("result_json"))
    return trip


def ticket_from_row(row):
    if not row:
        return None
    ticket = json.loads(row["ticket_json"])
    ticket["id"] = row["id"]
    ticket["source"] = row["source"]
    ticket["destination"] = row["destination"]
    ticket["travel_date"] = row["travel_date"]
    ticket["vehicle_type"] = row["vehicle_type"]
    return ticket


def row_to_dicts(rows):
    return [dict(row) for row in rows]


def get_booking(booking_id):
    connection = get_db()
    row = connection.execute(
        """
        SELECT b.id, b.user_id, b.status, b.created_at, b.cancelled_at,
               tr.id AS ticket_result_id, tr.source, tr.destination, tr.travel_date, tr.vehicle_type,
               tr.ticket_json
        FROM bookings b
        JOIN ticket_results tr ON tr.id = b.ticket_result_id
        WHERE b.id = %s
        """,
        (booking_id,),
    ).fetchone()

    if not row:
        connection.close()
        return None

    booking = dict(row)
    ticket = json.loads(booking.pop("ticket_json"))
    ticket["id"] = row["ticket_result_id"]
    ticket["source"] = row["source"]
    ticket["destination"] = row["destination"]
    ticket["travel_date"] = row["travel_date"]
    ticket["vehicle_type"] = row["vehicle_type"]
    booking["ticket"] = ticket

    passengers = connection.execute(
        "SELECT name, seat FROM passengers WHERE booking_id = %s ORDER BY id",
        (booking_id,),
    ).fetchall()
    booking["passengers"] = [dict(item) for item in passengers]

    connection.close()
    return booking


def get_user_bookings(user_id, limit=None):
    connection = get_db()
    sql = """
        SELECT b.id, b.user_id, b.status, b.created_at, b.cancelled_at,
               tr.id AS ticket_result_id,
               tr.source, tr.destination, tr.travel_date, tr.vehicle_type,
               tr.ticket_json
        FROM bookings b
        JOIN ticket_results tr ON tr.id = b.ticket_result_id
        WHERE b.user_id = %s
        ORDER BY b.created_at DESC
    """
    params = [user_id]

    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    rows = connection.execute(sql, params).fetchall()
    bookings = []

    for row in rows:
        booking = dict(row)
        ticket = json.loads(booking.pop("ticket_json"))
        ticket["id"] = row["ticket_result_id"]
        ticket["source"] = row["source"]
        ticket["destination"] = row["destination"]
        ticket["travel_date"] = row["travel_date"]
        ticket["vehicle_type"] = row["vehicle_type"]
        booking["ticket"] = ticket

        passengers = connection.execute(
            "SELECT name, seat FROM passengers WHERE booking_id = %s ORDER BY id",
            (booking["id"],),
        ).fetchall()
        booking["passengers"] = [dict(item) for item in passengers]
        bookings.append(booking)

    connection.close()
    return bookings


# -------------------------------------------------------------------
# GROQ HELPERS
# -------------------------------------------------------------------

def clean_json_text(text):
    text = (text or "").strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def require_groq():
    if groq_client is None:
        raise RuntimeError(
            "Groq API is not configured. Add GROQ_API_KEY to your .env file."
        )


def generate_json_with_groq(prompt):
    require_groq()

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an AI travel planning assistant. "
                    "Return ONLY valid JSON when JSON is requested. "
                    "Do not use Markdown or code blocks."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )

    text = response.choices[0].message.content
    if not text:
        raise RuntimeError("Groq returned an empty response.")

    text = clean_json_text(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise RuntimeError("Groq returned invalid JSON.")


def amadeus_access_token():
    if not LIVE_PRICING_ENABLED:
        return None
    response = requests.post(
        f"{AMADEUS_BASE_URL}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": AMADEUS_CLIENT_ID,
            "client_secret": AMADEUS_CLIENT_SECRET,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("access_token")


def amadeus_get(path, token, params=None):
    response = requests.get(
        f"{AMADEUS_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


def find_amadeus_location(keyword, token):
    data = amadeus_get(
        "/v1/reference-data/locations",
        token,
        {"subType": "CITY,AIRPORT", "keyword": keyword, "page[limit]": 10},
    )
    locations = data.get("data", [])
    # Prefer CITY results, then AIRPORT results. Return the IATA code.
    locations.sort(key=lambda x: 0 if x.get("subType") == "CITY" else 1)
    for item in locations:
        code = item.get("iataCode")
        if code:
            return code
    return None


def get_live_transport_quote(source, destination, travel_date, days, travelers):
    """Return a live flight quote when an Amadeus provider is configured."""
    if not LIVE_PRICING_ENABLED:
        return None

    token = amadeus_access_token()
    origin = find_amadeus_location(source, token)
    destination_code = find_amadeus_location(destination, token)
    if not origin or not destination_code:
        return None

    departure = datetime.strptime(travel_date, "%Y-%m-%d").date()
    return_date = departure + timedelta(days=max(days - 1, 1))

    params = {
        "originLocationCode": origin,
        "destinationLocationCode": destination_code,
        "departureDate": departure.isoformat(),
        "returnDate": return_date.isoformat(),
        "adults": travelers,
        "currencyCode": "INR",
        "max": 5,
    }
    data = amadeus_get("/v2/shopping/flight-offers", token, params)
    offers = data.get("data", [])
    prices = []
    for offer in offers:
        try:
            prices.append(float(offer["price"]["grandTotal"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not prices:
        return None

    # Use the lowest returned live round-trip offer for the planning budget.
    return {
        "amount": round(min(prices)),
        "currency": "INR",
        "source": "Amadeus live flight offer",
        "checked_at": now(),
        "origin_code": origin,
        "destination_code": destination_code,
    }


def get_live_hotel_quote(destination, travel_date, days, travelers):
    """Return a live hotel quote for the destination when Amadeus is configured."""
    if not LIVE_PRICING_ENABLED or days <= 1:
        return None

    token = amadeus_access_token()
    city_code = find_amadeus_location(destination, token)
    if not city_code:
        return None

    check_in = datetime.strptime(travel_date, "%Y-%m-%d").date()
    check_out = check_in + timedelta(days=days - 1)
    rooms = max(1, (travelers + 1) // 2)

    hotel_list = amadeus_get(
        "/v1/reference-data/locations/hotels/by-city",
        token,
        {"cityCode": city_code, "radius": 20, "radiusUnit": "KM", "hotelSource": "ALL"},
    )
    hotels = [x.get("hotelId") for x in hotel_list.get("data", []) if x.get("hotelId")][:10]
    if not hotels:
        return None

    offers_data = amadeus_get(
        "/v3/shopping/hotel-offers",
        token,
        {
            "hotelIds": ",".join(hotels),
            "checkInDate": check_in.isoformat(),
            "checkOutDate": check_out.isoformat(),
            "adults": max(1, travelers),
            "roomQuantity": rooms,
            "currency": "INR",
            "bestRateOnly": "true",
        },
    )

    prices = []
    for hotel in offers_data.get("data", []):
        for offer in hotel.get("offers", []):
            try:
                prices.append(float(offer["price"]["total"]))
            except (KeyError, TypeError, ValueError):
                continue
    if not prices:
        return None

    return {
        "amount": round(min(prices)),
        "currency": "INR",
        "source": "Amadeus live hotel offer",
        "checked_at": now(),
        "nights": days - 1,
        "rooms": rooms,
    }


def apply_live_pricing(result, source, destination, travel_date, days, travelers):
    """Replace AI transport/hotel estimates with provider quotes when available."""
    pricing = {
        "mode": "estimated",
        "checked_at": now(),
        "live_components": [],
        "estimated_components": ["food", "local_transport", "activities"],
        "note": "Live provider pricing was not available; remaining values are estimates.",
    }

    breakdown = result.get("budget_breakdown", {}) or {}
    live_transport = None
    live_hotel = None

    if LIVE_PRICING_ENABLED:
        try:
            live_transport = get_live_transport_quote(
                source, destination, travel_date, days, travelers
            )
        except Exception:
            live_transport = None
        try:
            live_hotel = get_live_hotel_quote(
                destination, travel_date, days, travelers
            )
        except Exception:
            live_hotel = None

    if live_transport:
        breakdown["outbound_transport"] = round(live_transport["amount"] / 2)
        breakdown["return_transport"] = round(live_transport["amount"] - breakdown["outbound_transport"])
        pricing["live_components"].append("round_trip_flight")
        pricing["flight"] = live_transport
    else:
        pricing["estimated_components"].extend(["outbound_transport", "return_transport"])

    if live_hotel:
        breakdown["hotel"] = live_hotel["amount"]
        pricing["live_components"].append("hotel")
        pricing["hotel"] = live_hotel
    else:
        pricing["estimated_components"].append("hotel")

    if pricing["live_components"]:
        pricing["mode"] = "mixed_live_and_estimated"
        pricing["note"] = (
            "Live flight/hotel offers were used where available. Food, local transport "
            "and activities remain planning estimates. Provider prices can change."
        )

    total = 0.0
    for key in [
        "outbound_transport", "return_transport", "hotel",
        "food", "local_transport", "activities",
    ]:
        try:
            breakdown[key] = float(breakdown.get(key, 0) or 0)
        except (TypeError, ValueError):
            breakdown[key] = 0.0
        total += breakdown[key]

    result["budget_breakdown"] = breakdown
    result["budget_in_inr"] = round(total)
    result["pricing"] = pricing
    return result


def generate_ai_trip(source, destination, travel_date, days, travelers, travel_style, interests):
    prompt = f"""
You are an AI travel planning assistant.

Create a practical START-TO-END travel plan.

Starting location: {source}
Destination: {destination}
Travel date: {travel_date}
Number of days: {days}
Number of travelers: {travelers}
Travel style: {travel_style}
Interests: {interests or "general sightseeing"}

IMPORTANT:
- The travel date is important and must influence the approximate budget.
- The same source, destination, travel date, number of travelers, days, travel style and interests should produce a consistent planning estimate when the application reuses this search.
- A different travel date may have a different approximate budget.
- Calculate the complete trip from the starting location to the destination and back.
- Include outbound transportation: {source} -> {destination}.
- Include return transportation: {destination} -> {source}.
- Include hotel, food, local transport and activities.
- Hotel cost should consider the number of nights (normally days - 1).
- Food should consider travelers and days.
- Consider travel style.
- The user does NOT enter a budget.
- The budget is an approximate planning estimate, not a guaranteed quote.
- Do not claim exact live prices or availability.

Return ONLY valid JSON with this exact structure:

{{
  "source": "{source}",
  "destination": "{destination}",
  "travel_date": "{travel_date}",
  "local_currency": "INR",
  "budget_in_inr": 0,
  "budget_breakdown": {{
    "outbound_transport": 0,
    "return_transport": 0,
    "hotel": 0,
    "food": 0,
    "local_transport": 0,
    "activities": 0
  }},
  "places_to_visit": ["place 1", "place 2", "place 3"],
  "itinerary": [
    {{
      "day": 1,
      "title": "string",
      "activities": ["activity 1", "activity 2"]
    }}
  ],
  "important_tips": ["tip 1", "tip 2"]
}}

Budget rules:
- All numeric budget values must be numbers.
- budget_in_inr should approximately equal the sum of the breakdown.
- Use INR as the main budget currency.
"""

    result = generate_json_with_groq(prompt)
    result["source"] = source
    result["destination"] = destination
    result["travel_date"] = travel_date

    breakdown = result.get("budget_breakdown", {}) or {}
    keys = [
        "outbound_transport",
        "return_transport",
        "hotel",
        "food",
        "local_transport",
        "activities",
    ]
    total = 0.0
    for key in keys:
        try:
            value = float(breakdown.get(key, 0) or 0)
        except (TypeError, ValueError):
            value = 0.0
        breakdown[key] = value
        total += value

    result["budget_breakdown"] = breakdown
    result["budget_in_inr"] = round(total)
    result = apply_live_pricing(
        result, source, destination, travel_date, days, travelers
    )
    return result


def search_live_tickets(source, destination, travel_date, vehicle_type):
    """
    Groq does not provide Google Search grounding in this implementation.
    This function therefore returns AI-generated planning options only.
    They must NOT be presented as verified live availability.
    """
    requested_type = vehicle_type or "All"

    prompt = f"""
You are a travel ticket planning assistant.

Create approximate travel-option suggestions for:
From: {source}
To: {destination}
Travel date: {travel_date}
Transport type: {requested_type}

IMPORTANT:
- These are planning estimates only, NOT live search results.
- The application stores the generated options for this exact route/date/type and reuses them for all users.
- Never claim that prices, schedules, seats, or availability are currently verified.
- Use "Check availability" for availability.
- If you cannot reasonably estimate a value, use null.
- Do not invent real booking URLs. Set source_url to null.
- If transport type is not All, only return that type.
- Return up to 10 reasonable options.

Return ONLY valid JSON:
{{
  "tickets": [
    {{
      "transport_type": "Train",
      "operator": "Estimated operator/category",
      "service_name": "Example service",
      "departure_time": "18:00",
      "arrival_time": "08:00",
      "price": 1200,
      "currency": "INR",
      "availability": "Check availability",
      "duration": "14h",
      "source_url": null
    }}
  ]
}}
"""

    result = generate_json_with_groq(prompt)
    tickets = result.get("tickets", [])
    return tickets if isinstance(tickets, list) else []


# -------------------------------------------------------------------
# PUBLIC ROUTES
# -------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not name or not email or not password:
        flash("All fields are required.", "danger")
        return redirect(url_for("register"))

    if password != confirm_password:
        flash("Passwords do not match.", "danger")
        return redirect(url_for("register"))

    if len(password) < 6:
        flash("Password must contain at least 6 characters.", "danger")
        return redirect(url_for("register"))

    connection = get_db()
    existing = connection.execute(
        "SELECT id FROM users WHERE email = %s",
        (email,),
    ).fetchone()

    if existing:
        connection.close()
        flash("An account with this email already exists.", "danger")
        return redirect(url_for("login"))

    connection.execute(
        """
        INSERT INTO users (id, name, email, password, role, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            str(uuid.uuid4()),
            name,
            email,
            generate_password_hash(password),
            "user",
            now(),
        ),
    )
    connection.commit()
    connection.close()

    flash("Registration successful. Please login.", "success")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    connection = get_db()
    user = connection.execute(
        "SELECT * FROM users WHERE email = %s",
        (email,),
    ).fetchone()
    connection.close()

    if not user or not check_password_hash(user["password"], password):
        flash("Invalid email or password.", "danger")
        return redirect(url_for("login"))

    session.clear()
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["user_email"] = user["email"]
    session["role"] = user["role"]

    flash("Login successful.", "success")

    if user["role"] == "admin":
        return redirect(url_for("admin"))

    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


# -------------------------------------------------------------------
# DASHBOARD
# -------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    connection = get_db()

    trip_rows = connection.execute(
        "SELECT * FROM trips WHERE user_id = %s ORDER BY created_at DESC LIMIT 5",
        (user_id,),
    ).fetchall()
    trips = [trip_from_row(row) for row in trip_rows]

    connection.close()

    bookings = get_user_bookings(user_id, limit=5)

    connection = get_db()
    trip_count = connection.execute(
        "SELECT COUNT(*) AS count FROM trips WHERE user_id = %s",
        (user_id,),
    ).fetchone()["count"]
    booking_count = connection.execute(
        "SELECT COUNT(*) AS count FROM bookings WHERE user_id = %s",
        (user_id,),
    ).fetchone()["count"]
    connection.close()

    return render_template(
        "dashboard.html",
        trip_count=trip_count,
        booking_count=booking_count,
        trips=trips,
        bookings=bookings,
    )


# -------------------------------------------------------------------
# AI TRAVEL PLANNER
# -------------------------------------------------------------------

@app.route("/planner", methods=["GET", "POST"])
@login_required
def planner():
    if request.method == "GET":
        return render_template("planner.html")

    source = request.form.get("source", "").strip()
    destination = request.form.get("destination", "").strip()
    travel_date = request.form.get("travel_date", "").strip()
    days_text = request.form.get("days", "").strip()
    travelers_text = request.form.get("travelers", "").strip()
    travel_style = request.form.get("travel_style", "Standard").strip()
    interests = request.form.get("interests", "").strip()

    if not source:
        flash("Please enter your starting location.", "danger")
        return redirect(url_for("planner"))
    if not destination:
        flash("Please enter a destination.", "danger")
        return redirect(url_for("planner"))
    if not travel_date:
        flash("Please select a travel date.", "danger")
        return redirect(url_for("planner"))

    try:
        datetime.strptime(travel_date, "%Y-%m-%d")
        days = int(days_text)
        travelers = int(travelers_text)
    except ValueError:
        flash("Travel date, days and travelers must be valid values.", "danger")
        return redirect(url_for("planner"))

    if days < 1 or days > 30:
        flash("Days must be between 1 and 30.", "danger")
        return redirect(url_for("planner"))
    if travelers < 1 or travelers > 20:
        flash("Travelers must be between 1 and 20.", "danger")
        return redirect(url_for("planner"))
    if travel_style not in {"Budget", "Standard", "Luxury"}:
        travel_style = "Standard"

    # Normalize only for cache identity. The displayed values remain user-friendly.
    normalized_source = normalize_search_value(source)
    normalized_destination = normalize_search_value(destination)
    normalized_style = normalize_search_value(travel_style)
    normalized_interests = normalize_search_value(interests)
    search_key = planner_search_key(
        normalized_source,
        normalized_destination,
        travel_date,
        days,
        travelers,
        normalized_style,
        normalized_interests,
    )

    connection = get_db()

    # One shared planner result for every user. The search row is locked while
    # the first request generates the AI result, so concurrent users cannot
    # create different budgets/transport estimates for the same exact search.
    try:
        connection.connection.start_transaction()
        connection.execute(
            """INSERT INTO planner_searches
               (search_key, source, destination, travel_date, days, travelers,
                travel_style, interests, budget, result_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s)
               ON DUPLICATE KEY UPDATE search_key = search_key""",
            (
                search_key,
                normalized_source,
                normalized_destination,
                travel_date,
                days,
                travelers,
                normalized_style,
                normalized_interests,
                json.dumps({}),
                now(),
            ),
        )
        connection.commit()

        connection.connection.start_transaction()
        cache_row = connection.execute(
            "SELECT * FROM planner_searches WHERE search_key = %s FOR UPDATE",
            (search_key,),
        ).fetchone()

        # Existing cached result: return exactly the same budget,
        # transportation and itinerary to every user.
        if cache_row and cache_row["result_json"] not in (None, "", "{}"): 
            result = json.loads(cache_row["result_json"])
            connection.commit()
            connection.close()

            # Save this user's search history without generating a new AI result.
            trip_id = str(uuid.uuid4())
            history = get_db()
            history.execute(
                """INSERT INTO trips
                   (id, user_id, source, destination, days, travel_date, travelers,
                    travel_style, interests, budget, result_json, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    trip_id, session["user_id"], source, destination, days,
                    travel_date, travelers, travel_style, interests,
                    result.get("budget_in_inr", 0), json.dumps(result), now(),
                ),
            )
            history.commit()
            history.close()

            trip = {
                "id": trip_id,
                "user_id": session["user_id"],
                "source": source,
                "destination": destination,
                "days": days,
                "travel_date": travel_date,
                "travelers": travelers,
                "travel_style": travel_style,
                "interests": interests,
                "budget": result.get("budget_in_inr", 0),
                "created_at": now(),
                "result": result,
                "shared_search": True,
                "search_key": search_key,
            }
            return render_template("planner_result.html", trip=trip, result=result, shared_search=True)

        # No cached result exists. Release/keep the row lock while generating
        # the AI result inside the transaction so another identical request waits.
        result = generate_ai_trip(
            source=source,
            destination=destination,
            travel_date=travel_date,
            days=days,
            travelers=travelers,
            travel_style=travel_style,
            interests=interests,
        )
        budget = result.get("budget_in_inr", 0)

        connection.execute(
            """UPDATE planner_searches
               SET budget = %s, result_json = %s
               WHERE search_key = %s""",
            (budget, json.dumps(result), search_key),
        )
        connection.commit()
        connection.close()

    except Exception as error:
        connection.rollback()
        connection.close()
        flash(f"AI planner error: {error}", "danger")
        return redirect(url_for("planner"))

    # Record the search in the current user's history only after the shared
    # planner result has been created.
    trip_id = str(uuid.uuid4())
    created_at = now()
    history = get_db()
    history.execute(
        """INSERT INTO trips
           (id, user_id, source, destination, days, travel_date, travelers,
            travel_style, interests, budget, result_json, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            trip_id, session["user_id"], source, destination, days, travel_date,
            travelers, travel_style, interests, budget, json.dumps(result), created_at,
        ),
    )
    history.commit()
    history.close()

    trip = {
        "id": trip_id,
        "user_id": session["user_id"],
        "source": source,
        "destination": destination,
        "days": days,
        "travel_date": travel_date,
        "travelers": travelers,
        "travel_style": travel_style,
        "interests": interests,
        "budget": budget,
        "created_at": created_at,
        "result": result,
        "shared_search": True,
        "search_key": search_key,
    }

    return render_template(
        "planner_result.html",
        trip=trip,
        result=result,
        shared_search=True,
    )


# -------------------------------------------------------------------
# TICKET SEARCH
# -------------------------------------------------------------------

@app.route("/tickets", methods=["GET", "POST"])
@login_required
def tickets():
    if request.method == "GET":
        return render_template("tickets.html", tickets=[], searched=False, filters={})

    source = request.form.get("source", "").strip()
    destination = request.form.get("destination", "").strip()
    travel_date = request.form.get("date", "").strip()
    vehicle_type = request.form.get("type", "All").strip() or "All"

    if not source or not destination or not travel_date:
        flash("Please enter From, To and travel date.", "danger")
        return redirect(url_for("tickets"))

    try:
        datetime.strptime(travel_date, "%Y-%m-%d")
    except ValueError:
        flash("Please select a valid travel date.", "danger")
        return redirect(url_for("tickets"))

    search_key = ticket_search_key(source, destination, travel_date, vehicle_type)
    connection = get_db()

    # One shared search record is used by every user. FOR UPDATE serializes
    # concurrent searches for the same route/date/type so two users cannot
    # create different inventories for the same search at the same time.
    connection.connection.start_transaction()
    try:
        connection.execute(
            """INSERT INTO ticket_searches
               (search_key, source, destination, travel_date, vehicle_type, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE search_key = VALUES(search_key)""",
            (search_key, source, destination, travel_date, vehicle_type, now()),
        )
        connection.commit()

        connection.connection.start_transaction()
        connection.execute(
            "SELECT * FROM ticket_searches WHERE search_key = %s FOR UPDATE",
            (search_key,),
        ).fetchone()

        rows = connection.execute(
            "SELECT * FROM ticket_results WHERE search_key = %s ORDER BY created_at ASC, id ASC",
            (search_key,),
        ).fetchall()

        if rows:
            normalized = [ticket_from_row(row) for row in rows]
            connection.commit()
            connection.close()
            session["ticket_result_ids"] = [item["id"] for item in normalized]
            return render_template(
                "tickets.html", tickets=normalized, searched=True,
                filters={"source": source, "destination": destination, "date": travel_date, "type": vehicle_type},
            )

        # Generate the shared inventory only once for this exact search key.
        found_tickets = search_live_tickets(source, destination, travel_date, vehicle_type)
        normalized = []
        for item in found_tickets:
            if not isinstance(item, dict):
                continue
            ticket_id = str(uuid.uuid4())
            item = dict(item)
            item["id"] = ticket_id
            item["source"] = source
            item["destination"] = destination
            item["travel_date"] = travel_date
            item["vehicle_type"] = vehicle_type
            item["search_key"] = search_key
            item.setdefault("seat_capacity", 40)

            connection.execute(
                """INSERT INTO ticket_results
                   (id, user_id, search_key, source, destination, travel_date, vehicle_type, ticket_json, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (ticket_id, session["user_id"], search_key, source, destination,
                 travel_date, vehicle_type, json.dumps(item), now()),
            )
            normalized.append(item)

        connection.commit()
    except Exception as error:
        connection.rollback()
        connection.close()
        flash(f"Ticket search error: {error}", "danger")
        return render_template(
            "tickets.html", tickets=[], searched=True,
            filters={"source": source, "destination": destination, "date": travel_date, "type": vehicle_type},
        )

    connection.close()
    session["ticket_result_ids"] = [item["id"] for item in normalized]
    return render_template(
        "tickets.html", tickets=normalized, searched=True,
        filters={"source": source, "destination": destination, "date": travel_date, "type": vehicle_type},
    )


# -------------------------------------------------------------------
# BOOK A TICKET SEARCH RESULT
# -------------------------------------------------------------------

@app.route("/book/<ticket_id>", methods=["GET", "POST"])
@login_required
def book(ticket_id):
    # Ticket results are shared inventory, so user_id is intentionally NOT
    # part of this lookup. Any logged-in user can book a shared ticket.
    connection = get_db()
    ticket_row = connection.execute(
        "SELECT * FROM ticket_results WHERE id = %s",
        (ticket_id,),
    ).fetchone()
    connection.close()

    ticket = ticket_from_row(ticket_row)
    if not ticket:
        flash("Ticket result was not found. Please search again.", "warning")
        return redirect(url_for("tickets"))

    if request.method == "GET":
        connection = get_db()
        booked_rows = connection.execute(
            "SELECT seat FROM booked_seats WHERE ticket_result_id = %s ORDER BY seat",
            (ticket_id,),
        ).fetchall()
        connection.close()
        booked_seats = {str(row["seat"]) for row in booked_rows}
        return render_template("book.html", ticket=ticket, booked_seats=booked_seats)

    passenger_names = [name.strip() for name in request.form.getlist("passenger_name") if name.strip()]
    seat_numbers = [seat.strip() for seat in request.form.getlist("seat_number") if seat.strip()]

    if not passenger_names:
        flash("Add at least one passenger.", "danger")
        return redirect(url_for("book", ticket_id=ticket_id))
    if len(passenger_names) != len(seat_numbers):
        flash("Each passenger must have exactly one seat.", "danger")
        return redirect(url_for("book", ticket_id=ticket_id))
    if len(passenger_names) > 10:
        flash("Maximum 10 passengers per booking.", "danger")
        return redirect(url_for("book", ticket_id=ticket_id))
    if len(set(name.lower() for name in passenger_names)) != len(passenger_names):
        flash("Passenger names must be unique.", "danger")
        return redirect(url_for("book", ticket_id=ticket_id))
    if len(set(seat_numbers)) != len(seat_numbers):
        flash("Each passenger must have a different seat.", "danger")
        return redirect(url_for("book", ticket_id=ticket_id))

    connection = get_db()
    booking_id = "TP-" + uuid.uuid4().hex[:8].upper()
    created_at = now()

    try:
        # A transaction + unique(ticket_result_id, seat) makes the inventory
        # shared and prevents two users from buying the same seat concurrently.
        connection.connection.start_transaction()
        connection.execute(
            "SELECT id FROM ticket_results WHERE id = %s FOR UPDATE",
            (ticket_id,),
        ).fetchone()

        placeholders = ",".join("%s" for _ in seat_numbers)
        existing_seats = connection.execute(
            f"SELECT seat FROM booked_seats WHERE ticket_result_id = %s AND seat IN ({placeholders}) FOR UPDATE",
            [ticket_id] + seat_numbers,
        ).fetchall()
        if existing_seats:
            booked = ", ".join(str(row["seat"]) for row in existing_seats)
            connection.rollback()
            connection.close()
            flash(f"These seats are already booked: {booked}", "danger")
            return redirect(url_for("book", ticket_id=ticket_id))

        connection.execute(
            """INSERT INTO bookings (id, user_id, ticket_result_id, status, created_at)
               VALUES (%s, %s, %s, 'Booked', %s)""",
            (booking_id, session["user_id"], ticket_id, created_at),
        )
        connection.executemany(
            "INSERT INTO passengers (booking_id, name, seat) VALUES (%s, %s, %s)",
            [(booking_id, name, seat) for name, seat in zip(passenger_names, seat_numbers)],
        )
        connection.executemany(
            """INSERT INTO booked_seats (ticket_result_id, booking_id, seat, created_at)
               VALUES (%s, %s, %s, %s)""",
            [(ticket_id, booking_id, seat, created_at) for seat in seat_numbers],
        )
        connection.commit()
    except IntegrityError:
        connection.rollback()
        connection.close()
        flash("One or more selected seats were just booked by another user. Please choose different seats.", "danger")
        return redirect(url_for("book", ticket_id=ticket_id))
    except Exception as error:
        connection.rollback()
        connection.close()
        flash(f"Booking error: {error}", "danger")
        return redirect(url_for("book", ticket_id=ticket_id))

    connection.close()
    return redirect(url_for("confirmation", booking_id=booking_id))


@app.route("/confirmation/<booking_id>")
@login_required
def confirmation(booking_id):
    booking = get_booking(booking_id)

    if not booking or booking["user_id"] != session["user_id"]:
        flash("Booking not found.", "danger")
        return redirect(url_for("bookings"))

    return render_template("confirmation.html", booking=booking)


@app.route("/bookings")
@login_required
def bookings():
    user_bookings = get_user_bookings(session["user_id"])
    return render_template("bookings.html", bookings=user_bookings)


@app.route("/cancel-booking/<booking_id>", methods=["POST"])
@login_required
def cancel_booking(booking_id):
    connection = get_db()
    booking = connection.execute(
        "SELECT id, status FROM bookings WHERE id = %s AND user_id = %s",
        (booking_id, session["user_id"]),
    ).fetchone()

    if not booking:
        connection.close()
        flash("Booking not found.", "danger")
        return redirect(url_for("bookings"))

    if booking["status"] == "Cancelled":
        connection.close()
        flash("Booking is already cancelled.", "warning")
        return redirect(url_for("bookings"))

    connection.connection.start_transaction()
    connection.execute(
        """UPDATE bookings SET status = 'Cancelled', cancelled_at = %s
           WHERE id = %s""",
        (now(), booking_id),
    )
    connection.execute("DELETE FROM booked_seats WHERE booking_id = %s", (booking_id,))
    connection.commit()
    connection.close()

    flash(f"Booking {booking_id} has been cancelled.", "success")
    return redirect(url_for("bookings"))


# -------------------------------------------------------------------
# ADMIN
# -------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin():
    connection = get_db()

    total_users = connection.execute(
        "SELECT COUNT(*) AS count FROM users"
    ).fetchone()["count"]
    total_trips = connection.execute(
        "SELECT COUNT(*) AS count FROM trips"
    ).fetchone()["count"]
    total_bookings = connection.execute(
        "SELECT COUNT(*) AS count FROM bookings"
    ).fetchone()["count"]
    active_bookings = connection.execute(
        "SELECT COUNT(*) AS count FROM bookings WHERE status = 'Booked'"
    ).fetchone()["count"]

    users = row_to_dicts(
        connection.execute(
            "SELECT id, name, email, role, created_at FROM users ORDER BY created_at DESC"
        ).fetchall()
    )

    booking_rows = connection.execute(
        """
        SELECT b.id, b.status, b.created_at,
               u.name AS user_name, u.email AS user_email,
               tr.source, tr.destination, tr.travel_date, tr.vehicle_type,
               tr.ticket_json
        FROM bookings b
        JOIN users u ON u.id = b.user_id
        JOIN ticket_results tr ON tr.id = b.ticket_result_id
        ORDER BY b.created_at DESC
        """
    ).fetchall()

    admin_bookings = []
    for row in booking_rows:
        item = dict(row)
        item["ticket"] = json.loads(item.pop("ticket_json"))
        item["ticket"]["source"] = row["source"]
        item["ticket"]["destination"] = row["destination"]
        item["ticket"]["travel_date"] = row["travel_date"]
        item["ticket"]["vehicle_type"] = row["vehicle_type"]
        admin_bookings.append(item)

    connection.close()

    return render_template(
        "admin.html",
        total_users=total_users,
        total_trips=total_trips,
        total_bookings=total_bookings,
        active_bookings=active_bookings,
        users=users,
        bookings=admin_bookings,
    )


# -------------------------------------------------------------------
# ERROR HANDLERS
# -------------------------------------------------------------------

@app.errorhandler(404)
def page_not_found(error):
    return render_template("index.html"), 404


@app.errorhandler(500)
def internal_error(error):
    flash("Something went wrong. Please try again.", "danger")
    return redirect(url_for("index"))


if __name__ == "__main__":
    print("=" * 60)
    print("AI Travel Planner")
    print("=" * 60)
    print(f"Groq model: {GROQ_MODEL}")
    print(f"Groq configured: {'YES' if groq_client else 'NO'}")
    print(f"MySQL database: {MYSQL_DATABASE}@{MYSQL_HOST}:{MYSQL_PORT}")
    print("Open: http://127.0.0.1:5000")
    print("=" * 60)

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
    )
