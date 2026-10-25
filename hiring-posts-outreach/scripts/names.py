"""Geography for the name-origin tie-break: the countries a judge may answer with, and how far each is from the
candidate's country.

The guess itself is not made here. hiringposts.py marks each lead that needs one as `pending`, `origin-plan` lists
them, and a lightweight LLM subagent (references/origin.md) reads name, headline and company and answers with one
of COUNTRY's keys; `origin` stores the answer and the distance from this table orders the leads. Until judged, and
when the judge cannot tell, a lead sits at UNKNOWN_KM: after the Americas and Iberia, before Asia, so an unreadable
name is neither rewarded nor buried. The guess only orders the tier-1 "unclear" group: a stated pay or region always
outranks it, it never drops a lead, and it is never said or implied to the person.
"""

from __future__ import annotations

import math

COUNTRY = {  # country -> (lat, lon) used for the distance; capitals or population centroids
    "Brazil": (-15.8, -47.9), "Portugal": (38.7, -9.1),
    "Argentina": (-34.6, -58.4), "Uruguay": (-34.9, -56.2), "Paraguay": (-25.3, -57.6), "Chile": (-33.4, -70.7),
    "Bolivia": (-16.5, -68.1), "Peru": (-12.0, -77.0), "Ecuador": (-0.2, -78.5), "Colombia": (4.7, -74.1),
    "Venezuela": (10.5, -66.9), "Mexico": (19.4, -99.1), "Hispanic LATAM": (4.7, -74.1), "Spain": (40.4, -3.7),
    "US": (38.9, -77.0), "Canada": (43.7, -79.4), "UK": (51.5, -0.1), "Ireland": (53.3, -6.3),
    "Netherlands": (52.4, 4.9), "Belgium": (50.8, 4.4), "France": (48.9, 2.4), "Germany": (52.5, 13.4),
    "Switzerland": (46.9, 7.4), "Italy": (41.9, 12.5), "Greece": (38.0, 23.7), "Sweden": (59.3, 18.1),
    "Norway": (59.9, 10.8), "Denmark": (55.7, 12.6), "Finland": (60.2, 24.9), "Poland": (52.2, 21.0),
    "Czechia": (50.1, 14.4), "Hungary": (47.5, 19.0), "Romania": (44.4, 26.1), "Serbia": (44.8, 20.5),
    "Croatia": (45.8, 16.0), "Ukraine": (50.5, 30.5), "Russia": (55.8, 37.6), "Turkey": (39.9, 32.9),
    "Israel": (32.1, 34.8), "MENA": (30.0, 31.2), "Iran": (35.7, 51.4), "Nigeria": (9.1, 7.5), "Ghana": (5.6, -0.2),
    "Kenya": (-1.3, 36.8), "South Africa": (-26.2, 28.0), "India": (19.1, 72.9), "Pakistan": (33.7, 73.0),
    "Bangladesh": (23.8, 90.4), "China": (31.2, 121.5), "Korea": (37.6, 127.0), "Japan": (35.7, 139.7),
    "Vietnam": (10.8, 106.7), "Philippines": (14.6, 121.0), "Indonesia": (-6.2, 106.8), "Australia": (-33.9, 151.2),
}
HOME = COUNTRY["Brazil"]
UNKNOWN_KM = 9000  # where a pending or unreadable lead sorts: after the Americas and Iberia, before Asia

PENDING = {"country": None, "distance_km": None, "confidence": "none", "basis": "awaiting the origin judge", "by": "pending"}


def distance_km(country: str | None, home: tuple = HOME) -> int | None:
    if country not in COUNTRY:
        return None
    (a1, o1), (a2, o2) = home, COUNTRY[country]
    p1, p2 = math.radians(a1), math.radians(a2)
    d = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(o2 - o1) / 2) ** 2
    return int(round(2 * 6371 * math.asin(math.sqrt(d)), -1))


def canon(country: str | None) -> str | None:
    """A judge's answer folded onto a COUNTRY key: case-insensitive, a few common spellings accepted."""
    if not country:
        return None
    s = country.strip()
    low = s.lower()
    alias = {"usa": "US", "united states": "US", "united states of america": "US", "u.s.": "US", "america": "US",
             "united kingdom": "UK", "great britain": "UK", "england": "UK", "south korea": "Korea", "republic of korea": "Korea",
             "czech republic": "Czechia", "latam": "Hispanic LATAM", "latin america": "Hispanic LATAM",
             "hispanic": "Hispanic LATAM", "spanish latam": "Hispanic LATAM", "middle east": "MENA", "arab": "MENA",
             "lebanon": "MENA", "egypt": "MENA", "uae": "MENA", "saudi arabia": "MENA", "morocco": "MENA", "jordan": "MENA",
             "the netherlands": "Netherlands", "holland": "Netherlands", "persia": "Iran"}
    if low in alias:
        return alias[low]
    return next((k for k in COUNTRY if k.lower() == low), None)
