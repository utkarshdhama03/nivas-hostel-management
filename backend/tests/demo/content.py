"""The fixed wording of the demo data: who exists, what things are called, what people complain about.

Kept apart from seed.py so the story can be changed without touching the mechanics. Everything here is
invented. Emails use the reserved `.example` domain (RFC 2606), which can never receive mail, and phone
numbers start with 0, which no Indian mobile number does.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HostelSpec:
    code: str
    name: str
    resident_type: str  # boys | girls
    buildings: tuple[str, ...]
    floors: int
    doubles_per_floor: int
    triples_per_floor: int
    students: int

    @property
    def beds(self) -> int:
        return len(self.buildings) * self.floors * (2 * self.doubles_per_floor + 3 * self.triples_per_floor)


@dataclass(frozen=True)
class OrgSpec:
    slug: str
    name: str
    legal_name: str  # carries MARKER: "reset" refuses to delete an organization without it
    domain: str
    city: str
    state: str
    postal_code: str
    upi_id: str
    admin_name: str  # the organization's one Administrator (D76), who registers it
    hostels: tuple[HostelSpec, ...]
    rent_double: str = "6500.00"
    rent_triple: str = "5200.00"
    mess_fee: str = "3400.00"
    deposit: str = "10000.00"
    late_fee: str = "250.00"
    extras: bool = True  # the fuller story (exits, refunds, transfers, fines, waivers, a reversed payment)
    demo_students: tuple[str, ...] = field(default=())
    pending_requests: int = 0  # D74: student registration requests waiting for the Administrator


MARKER = "SAMPLE DEMO DATA"

DEMO_UNIVERSITY = OrgSpec(
    slug="demo-uni",  # not demo-university: that address belongs to a real organization in hms
    name="Demo University Hostels",
    legal_name=f"Demo University ({MARKER})",
    domain="demo-university.example",
    city="Pune",
    state="Maharashtra",
    postal_code="411007",
    upi_id="hostelfees.demo@upi",
    admin_name="Kavita Deshpande",
    pending_requests=5,  # D74: Demo University accepts registration requests
    hostels=(
        HostelSpec("ABH", "Aryabhata Boys Hostel", "boys", ("A", "B"), 3, 5, 3, 100),
        HostelSpec("GGH", "Gargi Girls Hostel", "girls", ("A", "B"), 3, 5, 3, 100),
    ),
)

SUNRISE = OrgSpec(
    slug="sunrise",
    name="Sunrise Girls Hostel",
    legal_name=f"Sunrise Girls Hostel ({MARKER})",
    domain="sunrise-hostel.example",
    city="Jaipur",
    state="Rajasthan",
    postal_code="302017",
    upi_id="sunrisehostel.demo@upi",
    admin_name="Nandini Rathore",
    hostels=(HostelSpec("SGH", "Sunrise Girls Hostel", "girls", ("A",), 2, 3, 4, 30),),
    rent_double="7200.00",
    rent_triple="5800.00",
    mess_fee="3600.00",
    deposit="8000.00",
    late_fee="200.00",
    extras=False,
)

PROGRAMS = (
    "B.Tech Computer Science",
    "B.Tech Mechanical",
    "B.Tech Electronics",
    "B.Sc Physics",
    "B.Com",
    "BBA",
    "M.Sc",
)

COMPLAINT_CATEGORIES = ("Plumbing", "Electrical", "Furniture", "Cleaning", "Internet", "Mess")

# category -> (title, description, resolution note)
COMPLAINTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "Plumbing": (
        (
            "Leaking tap in the washroom",
            "The tap near the second basin drips all night.",
            "Washer replaced; no drip now.",
        ),
        ("Flush not working", "The flush in the corner toilet does not fill.", "Float valve replaced."),
        (
            "Low water pressure",
            "Hardly any water on our floor in the morning.",
            "Blocked filter cleaned; pressure is back.",
        ),
        ("Geyser not heating", "No hot water since yesterday.", "Thermostat replaced and tested."),
    ),
    "Electrical": (
        ("Fan making noise", "The ceiling fan rattles at every speed.", "Bearing oiled and blade tightened."),
        ("Tube light flickering", "The light over my desk flickers.", "Starter and tube replaced."),
        ("Socket not working", "The socket beside the bed gives no power.", "Loose wire fixed; socket tested."),
        ("Corridor light off", "The corridor is dark outside our room.", "Bulb replaced."),
    ),
    "Furniture": (
        ("Broken chair", "One leg of my chair has come loose.", "Chair replaced from the store."),
        ("Cupboard lock jammed", "The cupboard key does not turn.", "Lock replaced; two new keys handed over."),
        ("Bed frame creaking", "The bed frame creaks and shifts.", "Bolts tightened."),
    ),
    "Cleaning": (
        ("Washroom not cleaned", "The washroom was not cleaned today.", "Cleaned; the roster was corrected."),
        (
            "Dustbin overflowing",
            "The floor dustbin has not been emptied for two days.",
            "Emptied; pickup is now daily.",
        ),
        ("Mosquitoes in the room", "Too many mosquitoes in the evening.", "Fogging done and the window mesh repaired."),
    ),
    "Internet": (
        ("Wi-Fi keeps dropping", "The Wi-Fi disconnects every few minutes.", "Access point restarted and moved."),
        ("Very slow internet at night", "Pages barely load after 9 pm.", "Bandwidth limit per device corrected."),
        ("Cannot log in to Wi-Fi", "My device is rejected at the login page.", "Device registered again."),
    ),
    "Mess": (
        (
            "Food was cold at dinner",
            "Dinner was served cold yesterday.",
            "Hot counter repaired; timings told to staff.",
        ),
        ("Drinking water cooler is warm", "The cooler in the mess gives warm water.", "Compressor serviced."),
        (
            "Need more breakfast variety",
            "The same breakfast three days running.",
            "Menu committee added two new items.",
        ),
    ),
}
STILL_OPEN_NOTE = "Still not fixed; it happened again last night."

LEAVE_REASONS_HOME = (
    ("Going home for the weekend", "Home"),
    ("Sister's wedding", "Home"),
    ("Family function", "Home"),
    ("Doctor's appointment at home", "Home"),
    ("Festival at home", "Home"),
    ("Parents are visiting the city", "Relative's house"),
)
LEAVE_REASONS_LOCAL = (
    ("Buying books and stationery", "City market"),
    ("Bank work", "Bank branch"),
    ("Dinner with classmates", "Restaurant near the campus"),
    ("Medical shop and clinic", "City clinic"),
    ("Railway ticket booking", "Railway station"),
)
LEAVE_REJECTIONS = ("Internal exam the next morning", "Too many leaves this month; please meet the hostel office")

ANNOUNCEMENTS = (
    # (days before today, audience, hostel index or None, title, body)
    (
        88,
        "everyone",
        None,
        "Welcome to the new session",
        "Mess timings and the hostel rules are on the notice board.",
    ),
    (
        70,
        "students",
        None,
        "Fee payment reminder",
        "Monthly fees are due by the 11th. Pay by UPI from the Fees page, or at the office.",
    ),
    (
        55,
        "students",
        0,
        "Water supply maintenance",
        "No water in this hostel on Sunday from 10:00 to 13:00 (tank cleaning).",
    ),
    (
        41,
        "everyone",
        None,
        "Anti-ragging committee",
        "Committee members and helpline numbers are displayed in each hostel's lobby.",
    ),
    (
        27,
        "students",
        None,
        "Room inspection next week",
        "The hostel office will inspect rooms next week. Keep electrical fittings as issued.",
    ),
    (13, "staff", None, "Office meeting on Friday", "Administrators: meeting in the office at 16:00 on Friday."),
    (5, "students", None, "Festival dinner", "A special dinner will be served in the mess this Saturday at 20:00."),
    (
        1,
        "everyone",
        None,
        "Fire drill tomorrow",
        "A fire drill will be held tomorrow at 17:00. Assemble at the ground.",
    ),
)

MENU = {
    "breakfast": (
        "Poha, tea",
        "Idli, sambar",
        "Aloo paratha, curd",
        "Upma, coffee",
        "Bread, omelette or banana",
        "Puri bhaji",
        "Dosa, chutney",
    ),
    "lunch": (
        "Rice, dal, mixed vegetable, roti",
        "Rajma, rice, salad",
        "Chole, roti, rice",
        "Sambar rice, cabbage, papad",
        "Kadhi, rice, aloo jeera",
        "Veg pulao, raita",
        "Dal fry, bhindi, roti",
    ),
    "snacks": ("Tea, biscuits", "Samosa, tea", "Bhel", "Vada pav", "Corn chaat", "Pakora, tea", "Fruit"),
    "dinner": (
        "Roti, paneer, dal",
        "Roti, egg curry or mixed veg, rice",
        "Khichdi, kadhi, papad",
        "Roti, chana masala, rice",
        "Fried rice, manchurian",
        "Roti, dal makhani, jeera rice",
        "Pav bhaji",
    ),
}

FINES = (
    ("Broken window pane", "600.00", "damage", "Window pane broken while playing in the corridor"),
    ("Lost room key", "150.00", "other", "Duplicate key made"),
    ("Damaged study chair", "450.00", "damage", "Chair leg broken"),
)


# People's names (decided 11 November): hand-picked, not Faker, whose en_IN list also produced
# odd or non-Indian names. Grouped by community so a first name and a surname always belong together
# (no "Gurpreet Iyer"). The seeded random generator picks a group by its weight, then names from it, so
# the same people come out on every run. Faker still makes the street names and cities.
@dataclass(frozen=True)
class NameGroup:
    weight: int
    male: tuple[str, ...]
    female: tuple[str, ...]
    surnames: tuple[str, ...]


NAME_GROUPS = (
    NameGroup(  # Hindi-speaking north
        30,
        ("Aarav", "Abhishek", "Aditya", "Ajay", "Akash", "Aman", "Amit", "Ankit", "Ashish", "Deepak", "Gaurav",
         "Harsh", "Kartik", "Manish", "Mohit", "Nikhil", "Rahul", "Rajat", "Rohit", "Shivam", "Sumit", "Varun"),
        ("Aditi", "Akanksha", "Ananya", "Anjali", "Ankita", "Divya", "Jyoti", "Khushi", "Kriti", "Megha", "Neha",
         "Nidhi", "Pooja", "Riya", "Ruchi", "Sakshi", "Shreya", "Shruti", "Swati", "Tanvi"),
        ("Sharma", "Verma", "Gupta", "Agarwal", "Mishra", "Tiwari", "Pandey", "Dubey", "Tripathi", "Saxena",
         "Srivastava", "Yadav", "Chauhan", "Rathore"),
    ),
    NameGroup(  # Maharashtra and Gujarat
        20,
        ("Omkar", "Pranav", "Sachin", "Sandeep", "Saurabh", "Tushar", "Vinay", "Yash", "Yogesh", "Jayesh",
         "Kunal", "Nitin", "Parth", "Dhruv", "Hardik", "Chirag"),
        ("Aishwarya", "Gayatri", "Madhuri", "Pallavi", "Prachi", "Sanjana", "Sneha", "Sonal", "Vaishnavi",
         "Mitali", "Isha", "Bhavna", "Hetal", "Krupa"),
        ("Kulkarni", "Deshmukh", "Patil", "Pawar", "Jadhav", "Joshi", "Naik", "Gokhale", "Shah", "Patel",
         "Desai", "Mehta", "Trivedi", "Parikh"),
    ),
    NameGroup(  # the south
        20,
        ("Arjun", "Karthik", "Pradeep", "Rajesh", "Sanjay", "Sriram", "Venkat", "Vignesh", "Harish", "Naveen",
         "Suresh", "Arun", "Ganesh", "Ramesh"),
        ("Divya", "Kavitha", "Lakshmi", "Meera", "Revathi", "Sowmya", "Pavithra", "Deepa", "Anitha", "Keerthana",
         "Nandini", "Priya", "Shalini", "Vidya"),
        ("Iyer", "Iyengar", "Nair", "Menon", "Pillai", "Reddy", "Rao", "Naidu", "Krishnan", "Subramanian",
         "Raman", "Hegde"),
    ),
    NameGroup(  # Bengal
        10,
        ("Arnab", "Debashish", "Soumya", "Subhajit", "Ritwik", "Anirban", "Sayan", "Indranil"),
        ("Ananya", "Debolina", "Moumita", "Paromita", "Rituparna", "Sreya", "Tanushree", "Ishita"),
        ("Banerjee", "Chatterjee", "Mukherjee", "Das", "Bose", "Sen", "Ghosh", "Dutta"),
    ),
    NameGroup(  # Punjab
        8,
        ("Harpreet", "Gurpreet", "Manpreet", "Jaskaran", "Amandeep", "Navjot", "Simranjit"),
        ("Harleen", "Jaspreet", "Simran", "Navneet", "Gurleen", "Manpreet", "Rupinder"),
        ("Singh", "Gill", "Sandhu", "Grewal", "Dhillon", "Sidhu", "Bajwa"),
    ),
    NameGroup(  # Muslim families
        8,
        ("Abdul", "Imran", "Faisal", "Arif", "Irfan", "Sameer", "Zaid", "Adil"),
        ("Aisha", "Fatima", "Sana", "Zoya", "Ayesha", "Nazia", "Farah", "Sara"),
        ("Khan", "Qureshi", "Siddiqui", "Ansari", "Sheikh", "Hussain", "Ahmed", "Mirza"),
    ),
    NameGroup(  # Christian families
        4,
        ("Thomas", "John", "Alan", "Jerin", "Kevin", "Shibu"),
        ("Mary", "Sharon", "Ann", "Jincy", "Merin", "Teresa"),
        ("Fernandes", "Pinto", "George", "Mathew", "Varghese", "Joseph"),
    ),
)  # fmt: skip
