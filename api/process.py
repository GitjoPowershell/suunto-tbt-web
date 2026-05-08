import json
import math
import io
import urllib.request
import urllib.parse
import os
import sys

from flask import Flask, request, jsonify

sys.path.insert(0, os.path.dirname(__file__))
import gpxpy

app = Flask(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

WINDOW_M = 50
MIN_TURN_ANGLE = 15
MIN_SPACING_M = 40
SNAP_DIST_M = 25

_GPX_HEADER = '''<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="Suunto Routeplanner" version="1.1"
  xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd"
  xmlns="http://www.topografix.com/GPX/1/1"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:suunto-rp="http://www.suunto.com/routeplanner/gpx/1.0">
<metadata>
  <name>Suunto Route</name>
  <desc>Route created with Suunto Routeplanner</desc>
</metadata>

<!-- Route begin and end waypoints -->'''

# ── Geometry ───────────────────────────────────────────────────────────────────

def haversine_m(p1, p2):
    R = 6_371_000
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def bearing(p1, p2):
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def signed_turn(b_in, b_out):
    return (b_out - b_in + 180) % 360 - 180


def cumulative_distances(points):
    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(cum[-1] + haversine_m(points[i - 1], points[i]))
    return cum

# ── OSM junction detection ─────────────────────────────────────────────────────

def get_osm_junctions(points, padding=0.003):
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    s, n = min(lats) - padding, max(lats) + padding
    w, e = min(lons) - padding, max(lons) + padding

    query = f"""[out:json][timeout:55];
(way[highway]({s},{w},{n},{e}););
(._;>;);
out body;"""

    data_enc = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=data_enc,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "suunto-tbt/1.0"},
    )
    with urllib.request.urlopen(req, timeout=58) as resp:
        data = json.loads(resp.read())

    node_count: dict = {}
    for el in data["elements"]:
        if el["type"] == "way":
            for nid in el["nodes"]:
                node_count[nid] = node_count.get(nid, 0) + 1

    return [(el["lat"], el["lon"]) for el in data["elements"]
            if el["type"] == "node" and node_count.get(el["id"], 0) >= 2]


def snap_junctions_to_route(junctions, points):
    hits = set()
    for jlat, jlon in junctions:
        best_i, best_d = 0, float("inf")
        for i, pt in enumerate(points):
            d = haversine_m((jlat, jlon), pt)
            if d < best_d:
                best_d = d
                best_i = i
        if best_d <= SNAP_DIST_M:
            hits.add(best_i)
    return sorted(hits)

# ── Turn detection ─────────────────────────────────────────────────────────────

def _classify(turn_angle):
    a = abs(turn_angle)
    if turn_angle > 0:
        if a > 100: return "Sharp_right_turn", "Turn sharp right"
        if a > 45:  return "Right_turn",        "Turn right"
        return              "Slight_right_turn", "Turn slight right"
    else:
        if a > 100: return "Sharp_left_turn",   "Turn sharp left"
        if a > 45:  return "Left_turn",         "Turn left"
        return              "Slight_left_turn",  "Turn slight left"


def _turns_from_indices(points, indices, min_angle):
    n = len(points)
    cum = cumulative_distances(points)
    turns, last_d = [], -MIN_SPACING_M
    for i in indices:
        if i == 0 or i >= n - 1:
            continue
        d = cum[i]
        if d - last_d < MIN_SPACING_M:
            continue
        j = i
        while j > 0 and d - cum[j] < WINDOW_M:
            j -= 1
        k = i
        while k < n - 1 and cum[k] - d < WINDOW_M:
            k += 1
        turn = signed_turn(bearing(points[j], points[i]), bearing(points[i], points[k]))
        if abs(turn) < min_angle:
            continue
        last_d = d
        wpt_type, name = _classify(turn)
        turns.append((points[i][0], points[i][1], name, wpt_type))
    return turns


def detect_turns(points):
    try:
        junctions = get_osm_junctions(points)
        indices = snap_junctions_to_route(junctions, points)
        turns = _turns_from_indices(points, indices, MIN_TURN_ANGLE)
        return turns, "osm"
    except Exception:
        indices = list(range(1, len(points) - 1))
        turns = _turns_from_indices(points, indices, 30)
        return turns, "angle"

# ── GPX generation ─────────────────────────────────────────────────────────────

def _wpt(lat, lon, name, wpt_type, desc=True):
    d = "<desc>Auto-generated</desc>" if desc else ""
    return f'<wpt lat="{lat}" lon="{lon}"><name>{name}</name>{d}<type>{wpt_type}</type></wpt>'


def generate_suunto_gpx(points, turn_waypoints):
    begin, end = points[0], points[-1]
    lines = [
        _GPX_HEADER,
        _wpt(begin[0], begin[1], "Begin", "Begin"),
        _wpt(end[0], end[1], "End", "End"),
    ]
    if turn_waypoints:
        lines.append("<!-- Turn guidance waypoints -->")
        lines += [_wpt(lat, lon, name, t, desc=False) for lat, lon, name, t in turn_waypoints]
    lines += [
        "<!-- Route points -->", "<rte>",
        "  <name>Suunto Route</name>",
        "  <desc>Route created with Suunto Routeplanner</desc>",
        f"  <extensions><suunto-rp:waypointIndices>0,{len(points)-1}</suunto-rp:waypointIndices></extensions>",
    ]
    lines += [f'  <rtept lat="{lat}" lon="{lon}"><ele>{round(ele) if ele else 0}</ele></rtept>'
              for lat, lon, ele in points]
    lines += ["</rte>", "</gpx>"]
    return "\n".join(lines)

# ── Flask route ────────────────────────────────────────────────────────────────

@app.after_request
def cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


@app.route("/api/process", methods=["POST", "OPTIONS"])
def process():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    body = request.get_json(force=True)
    gpx_text = body.get("gpx", "")

    gpx = gpxpy.parse(io.StringIO(gpx_text))
    points = [
        (pt.latitude, pt.longitude, pt.elevation)
        for track in gpx.tracks for seg in track.segments for pt in seg.points
    ]
    if not points:
        points = [
            (pt.latitude, pt.longitude, pt.elevation)
            for route in gpx.routes for pt in route.points
        ]
    if not points:
        return jsonify({"error": "No track points found"}), 400

    turn_waypoints, method = detect_turns(points)
    gpx_content = generate_suunto_gpx(points, turn_waypoints)

    return jsonify({
        "gpxContent": gpx_content,
        "routePoints": [[p[0], p[1]] for p in points],
        "turnWaypoints": [[lat, lon, name] for lat, lon, name, _ in turn_waypoints],
        "pointCount": len(points),
        "turnCount": len(turn_waypoints),
        "method": method,
    })
