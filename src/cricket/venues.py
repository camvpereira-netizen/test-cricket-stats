"""Map Cricsheet venues/cities to the Test nation that hosts there.

Cricsheet has no country field, so this is a hand-built lookup by city (falling back
to a substring match on the venue name). West Indies is one Test side spread over
several countries, so Caribbean cities map to "West Indies". Cities with no Test
"home" side (e.g. UAE, used by Pakistan and Afghanistan) map to their real country,
which never equals a team name, so those matches count as neutral.
"""
from __future__ import annotations

CITY_HOST: dict[str, str] = {}

_HOSTS = {
    "England": [
        "London", "Birmingham", "Manchester", "Leeds", "Nottingham", "Southampton",
        "Chester-le-Street", "Cardiff", "Bristol", "Taunton", "Hove", "Canterbury",
        "Worcester", "Derby", "Northampton", "Leicester", "Uxbridge", "Sheffield",
    ],
    "Australia": [
        "Sydney", "Melbourne", "Brisbane", "Adelaide", "Perth", "Hobart", "Canberra",
        "Cairns", "Darwin", "Townsville", "Geelong", "Carrara", "Gold Coast", "Mackay",
    ],
    "India": [
        "Mumbai", "Delhi", "Kolkata", "Chennai", "Bangalore", "Bengaluru", "Hyderabad",
        "Mohali", "Chandigarh", "Nagpur", "Ahmedabad", "Kanpur", "Pune", "Rajkot", "Indore",
        "Ranchi", "Dharamsala", "Visakhapatnam", "Lucknow", "Cuttack", "Guwahati",
        "Thiruvananthapuram", "Greater Noida", "Dehradun", "Jaipur",
        # older names and grounds used before 2001
        "Bombay", "Madras", "Calcutta", "Jalandhar", "Mullanpur", "Dharamshala",
    ],
    "Pakistan": [
        "Karachi", "Lahore", "Rawalpindi", "Multan", "Faisalabad", "Peshawar", "Sialkot",
        "Sheikhupura", "Bahawalpur", "Gujranwala",
        "Dacca",  # East Pakistan until 1971; Tests in Dhaka since then are Bangladesh's
    ],
    "Sri Lanka": [
        "Colombo", "Galle", "Kandy", "Pallekele", "Dambulla", "Hambantota", "Moratuwa",
    ],
    "South Africa": [
        "Johannesburg", "Cape Town", "Durban", "Centurion", "Port Elizabeth", "Gqeberha",
        "Gqeberha (Port Elizabeth)", "East London", "Bloemfontein", "Potchefstroom",
        "Paarl", "Benoni", "Kimberley",
    ],
    "New Zealand": [
        "Auckland", "Wellington", "Christchurch", "Hamilton", "Napier", "Dunedin",
        "Mount Maunganui", "Queenstown", "Nelson", "Whangarei", "Tauranga",
    ],
    "West Indies": [
        "Kingston", "Bridgetown", "Barbados", "Port of Spain", "Trinidad", "Georgetown",
        "Guyana", "St John's", "Antigua", "North Sound", "Gros Islet", "St Lucia",
        "Basseterre", "St Kitts", "Kingstown", "St Vincent", "Roseau", "Dominica",
        "St George's", "St. George's", "Grenada", "Providence", "Jamaica", "Tarouba",
        "Saint Lucia", "Sabina Park",
    ],
    "Bangladesh": [
        "Dhaka", "Mirpur", "Chittagong", "Chattogram", "Sylhet", "Khulna", "Fatullah",
        "Bogra",
    ],
    "Zimbabwe": ["Harare", "Bulawayo", "Kwekwe"],
    "Ireland": ["Dublin", "Malahide", "Belfast", "Stormont"],
    # No home Test side: always neutral
    "United Arab Emirates": ["Dubai", "Abu Dhabi", "Sharjah"],
    "Oman": ["Muscat"],
}
for _host, _cities in _HOSTS.items():
    for _c in _cities:
        CITY_HOST[_c.lower()] = _host


def host_country(city: str | None, venue: str | None) -> str | None:
    """Return the hosting nation for a city/venue, or None if unknown."""
    if city and city.lower() in CITY_HOST:
        return CITY_HOST[city.lower()]
    text = f"{venue or ''} {city or ''}".lower()
    for c, host in CITY_HOST.items():
        if c in text:
            return host
    return None


def home_team(team1: str, team2: str, country: str | None) -> str | None:
    """The team playing at home, or None for a neutral or unknown venue."""
    if country == team1:
        return team1
    if country == team2:
        return team2
    return None
