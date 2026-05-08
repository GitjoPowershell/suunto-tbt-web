# GPX → Suunto TbT

Convert any GPX trace into a **turn-by-turn (TbT) compatible GPX** for Suunto watches (Race 2, Vertical, etc.).

Drop a GPX file → the app detects real trail forks from OpenStreetMap data → generates a Suunto-compatible route file with embedded turn waypoints → upload it to your Suunto account.

## How it works

1. **Parse** the original GPX track (no re-routing — follows the exact original path)
2. **Query OpenStreetMap** via Overpass API to find real path junctions/forks in the route area
3. **Detect turns** only at actual decision points (Left_turn, Right_turn, Slight_left_turn, etc.)
4. **Generate a Suunto GPX** with `creator="Suunto Routeplanner"` format, including:
   - Begin/End waypoints
   - Turn guidance waypoints (`<wpt>` with `<type>Right_turn</type>`, etc.)
   - Full route points (`<rtept>`) with elevation
   - `suunto-rp:waypointIndices` extension

The output is byte-for-byte compatible with files exported from [routeplanner.suunto.com](https://routeplanner.suunto.com).

## Deploy your own instance

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/YOUR_USERNAME/suunto-tbt-web)

Or manually:

```bash
npm i -g vercel
vercel
```

## Local development

```bash
pip install gpxpy
vercel dev
```

Then open [http://localhost:3000](http://localhost:3000).

## Output format

```xml
<wpt lat="48.xxx" lon="6.xxx"><name>Turn right</name><type>Right_turn</type></wpt>
<wpt lat="48.xxx" lon="6.xxx"><name>Turn left</name><type>Left_turn</type></wpt>
```

Supported turn types: `Right_turn`, `Left_turn`, `Slight_right_turn`, `Slight_left_turn`, `Sharp_right_turn`, `Sharp_left_turn`.

## Stack

- **Frontend**: Vanilla HTML + Leaflet.js (OSM tiles)
- **Backend**: Python 3.12 serverless function (Vercel)
- **Data**: [Overpass API](https://overpass-api.de) for OSM junction detection
- **GPX parsing**: [gpxpy](https://github.com/tkrajina/gpxpy)
