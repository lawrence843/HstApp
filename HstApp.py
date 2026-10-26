import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator


APP_DIRECTORY = Path(__file__).resolve().parent
DATABASE_FILE = APP_DIRECTORY / "hst_app.db"
JOB_STATUS_TYPES: dict[str, str] = {
    "1": "Working",
    "2": "Not_Working",
    "3": "Retired",
    "4": "House_Wife",
    "5": "Student",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    contact_no TEXT NOT NULL,
    contact_key TEXT NOT NULL,
    occupation_status TEXT NOT NULL,
    work_category TEXT NOT NULL,
    UNIQUE (name_key, contact_key)
);

CREATE TABLE IF NOT EXISTS apartments (
    id INTEGER PRIMARY KEY,
    apartment_no TEXT NOT NULL,
    apartment_key TEXT NOT NULL,
    tower TEXT NOT NULL,
    tower_key TEXT NOT NULL,
    owner_person_id INTEGER REFERENCES people(id),
    UNIQUE (tower_key, apartment_key)
);

CREATE TABLE IF NOT EXISTS family_members (
    id INTEGER PRIMARY KEY,
    apartment_id INTEGER NOT NULL REFERENCES apartments(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id),
    relation TEXT NOT NULL,
    UNIQUE (apartment_id, person_id)
);

CREATE TABLE IF NOT EXISTS tenancies (
    id INTEGER PRIMARY KEY,
    apartment_id INTEGER NOT NULL REFERENCES apartments(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id),
    start_date TEXT,
    end_date TEXT,
    CHECK (end_date IS NULL OR (start_date IS NOT NULL AND end_date >= start_date))
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_tenancy_per_person_and_apartment
ON tenancies (apartment_id, person_id)
WHERE end_date IS NULL;

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Person:
    name: str
    contact_no: str
    occupation_status: str
    work_category: str


@contextmanager
def open_database(database_file: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_file)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def validate_text(value: str, field_name: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty.")
    return cleaned


def normalize_key(value: str) -> str:
    return " ".join(value.casefold().split())


def normalize_contact(value: str) -> str:
    return re.sub(r"[\s()-]", "", value)


def validate_contact(value: str) -> str:
    cleaned = validate_text(value, "Contact number")
    if not re.fullmatch(r"\+?[0-9 ()-]+", cleaned):
        raise ValueError("Contact number may contain digits, spaces, +, - and parentheses only.")
    digits = re.sub(r"\D", "", cleaned)
    if not 7 <= len(digits) <= 15:
        raise ValueError("Contact number must contain between 7 and 15 digits.")
    return cleaned


def validate_date(value: str, field_name: str) -> str:
    cleaned = validate_text(value, field_name)
    try:
        parsed = date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format.") from exc
    if parsed.isoformat() != cleaned:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format.")
    return cleaned


def person_from_record(record: dict[str, Any], source: str) -> Person:
    try:
        name = validate_text(str(record["name"]), "Name")
        contact_no = validate_contact(str(record["contact_no"]))
        occupation_status = validate_text(str(record["occupation_status"]), "Occupation status")
        work_category = validate_text(str(record["work_category"]), "Work category")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid person record in {source}: {exc}") from exc
    return Person(name, contact_no, occupation_status, work_category)


def get_or_create_person(connection: sqlite3.Connection, person: Person) -> int:
    name_key = normalize_key(person.name)
    contact_key = normalize_contact(person.contact_no)
    row = connection.execute(
        "SELECT id FROM people WHERE name_key = ? AND contact_key = ?",
        (name_key, contact_key),
    ).fetchone()
    if row is not None:
        return int(row["id"])

    cursor = connection.execute(
        """
        INSERT INTO people (
            name, name_key, contact_no, contact_key, occupation_status, work_category
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            person.name,
            name_key,
            person.contact_no,
            contact_key,
            person.occupation_status,
            person.work_category,
        ),
    )
    return int(cursor.lastrowid)


def load_legacy_records(file_path: Path) -> list[dict[str, Any]] | None:
    if not file_path.exists():
        return None
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cannot import {file_path.name}: invalid JSON ({exc}).") from exc
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise ValueError(f"Cannot import {file_path.name}: expected a list of records.")
    return data


def initialize_database(database_file: Path, legacy_directory: Path) -> bool:
    database_file.parent.mkdir(parents=True, exist_ok=True)
    with open_database(database_file) as connection:
        connection.executescript(SCHEMA)
        imported_legacy_data = False
        if connection.execute(
            "SELECT 1 FROM app_metadata WHERE key = 'legacy_json_imported'"
        ).fetchone():
            return imported_legacy_data

        people_records = load_legacy_records(legacy_directory / "persons.json")
        apartment_records = load_legacy_records(legacy_directory / "apartments.json")
        if people_records is None and apartment_records is None:
            return imported_legacy_data

        existing_records = connection.execute(
            "SELECT (SELECT COUNT(*) FROM people) + (SELECT COUNT(*) FROM apartments)"
        ).fetchone()[0]
        if existing_records:
            raise ValueError(
                "Legacy JSON files were found, but the SQLite database already has records. "
                "Import them separately to avoid duplicate or conflicting data."
            )

        with connection:
            for record in people_records or []:
                get_or_create_person(connection, person_from_record(record, "persons.json"))

            for apartment_record in apartment_records or []:
                try:
                    apartment_no = validate_text(
                        str(apartment_record["apartment_no"]), "Apartment number"
                    )
                    tower = validate_text(str(apartment_record["tower"]), "Tower")
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"Invalid apartment record in apartments.json: {exc}") from exc

                owner_record = apartment_record.get("owner")
                if owner_record is None:
                    owner_id = None
                elif isinstance(owner_record, dict):
                    owner_id = get_or_create_person(
                        connection,
                        person_from_record(owner_record, "apartments.json owner"),
                    )
                else:
                    raise ValueError(
                        f"Invalid owner record for apartment {tower} {apartment_no}."
                    )
                cursor = connection.execute(
                    """
                    INSERT INTO apartments (
                        apartment_no, apartment_key, tower, tower_key, owner_person_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        apartment_no,
                        normalize_key(apartment_no),
                        tower,
                        normalize_key(tower),
                        owner_id,
                    ),
                )
                apartment_id = int(cursor.lastrowid)

                family_members = apartment_record.get("family_members", [])
                tenants = apartment_record.get("tenants", [])
                if not isinstance(family_members, list) or not isinstance(tenants, list):
                    raise ValueError(
                        f"Invalid resident lists for apartment {tower} {apartment_no}."
                    )

                for member in family_members:
                    if not isinstance(member, dict) or not isinstance(member.get("person"), dict):
                        raise ValueError(
                            f"Invalid family member record for apartment {tower} {apartment_no}."
                        )
                    relation = validate_text(str(member.get("relation", "")), "Family relation")
                    person = person_from_record(member["person"], "apartments.json family member")
                    person_id = get_or_create_person(connection, person)
                    if owner_id == person_id and normalize_key(relation) == "owner":
                        continue
                    connection.execute(
                        """
                        INSERT INTO family_members (apartment_id, person_id, relation)
                        VALUES (?, ?, ?)
                        """,
                        (apartment_id, person_id, relation),
                    )

                for tenant_record in tenants:
                    if not isinstance(tenant_record, dict):
                        raise ValueError(
                            f"Invalid tenant record for apartment {tower} {apartment_no}."
                        )
                    tenant = person_from_record(tenant_record, "apartments.json tenant")
                    person_id = get_or_create_person(connection, tenant)
                    connection.execute(
                        """
                        INSERT INTO tenancies (apartment_id, person_id, start_date, end_date)
                        VALUES (?, ?, NULL, NULL)
                        """,
                        (apartment_id, person_id),
                    )

            connection.execute(
                "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                ("legacy_json_imported", date.today().isoformat()),
            )
        return True


def collect_person_input() -> Person:
    name = validate_text(input("Enter person name: "), "Name")
    contact_no = validate_contact(input("Enter contact number: "))
    while True:
        print("1. Working")
        print("2. Not Working")
        print("3. Retired")
        print("4. House Wife")
        print("5. Student")
        choice = input("Select occupation status (1-5): ").strip()
        occupation_status = JOB_STATUS_TYPES.get(choice)
        if occupation_status is not None:
            break
        print("Please choose a number from 1 to 5.")
    work_category = validate_text(input("Enter work category: "), "Work category")
    return Person(name, contact_no, occupation_status, work_category)


def collect_tower_and_apartment() -> tuple[str, str]:
    tower = validate_text(input("Enter tower name: "), "Tower")
    apartment_no = validate_text(input("Enter apartment number: "), "Apartment number")
    return tower, apartment_no


def find_apartment(
    connection: sqlite3.Connection, tower: str, apartment_no: str
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, tower, apartment_no, owner_person_id
        FROM apartments
        WHERE tower_key = ? AND apartment_key = ?
        """,
        (normalize_key(tower), normalize_key(apartment_no)),
    ).fetchone()


def select_apartment(connection: sqlite3.Connection) -> sqlite3.Row:
    tower, apartment_no = collect_tower_and_apartment()
    apartment = find_apartment(connection, tower, apartment_no)
    if apartment is None:
        raise ValueError(f"No apartment {apartment_no} was found in {tower}.")
    return apartment


def add_apartment(connection: sqlite3.Connection) -> None:
    tower, apartment_no = collect_tower_and_apartment()
    if find_apartment(connection, tower, apartment_no) is not None:
        raise ValueError(f"Apartment {apartment_no} already exists in {tower}.")

    while True:
        has_owner = input("Add the owner now? (y/n): ").strip().casefold()
        if has_owner in {"y", "yes"}:
            owner = collect_person_input()
            break
        if has_owner in {"n", "no"}:
            owner = None
            break
        print("Please enter y or n.")
    with connection:
        owner_id = get_or_create_person(connection, owner) if owner else None
        connection.execute(
            """
            INSERT INTO apartments (
                apartment_no, apartment_key, tower, tower_key, owner_person_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                apartment_no,
                normalize_key(apartment_no),
                tower,
                normalize_key(tower),
                owner_id,
            ),
        )
    print(f"Apartment {apartment_no} in {tower} added.")


def add_family_member(connection: sqlite3.Connection) -> None:
    apartment = select_apartment(connection)
    person = collect_person_input()
    relation = validate_text(input("Enter their relation to the household: "), "Relation")
    with connection:
        person_id = get_or_create_person(connection, person)
        if connection.execute(
            "SELECT 1 FROM family_members WHERE apartment_id = ? AND person_id = ?",
            (apartment["id"], person_id),
        ).fetchone():
            raise ValueError("This person is already listed as a family member of that apartment.")
        connection.execute(
            """
            INSERT INTO family_members (apartment_id, person_id, relation)
            VALUES (?, ?, ?)
            """,
            (apartment["id"], person_id, relation),
        )
    print(f"Family member added to {apartment['apartment_no']} in {apartment['tower']}.")


def add_tenant(connection: sqlite3.Connection) -> None:
    apartment = select_apartment(connection)
    person = collect_person_input()
    start_date = validate_date(
        input(f"Tenancy start date (YYYY-MM-DD, default {date.today().isoformat()}): ").strip()
        or date.today().isoformat(),
        "Start date",
    )
    with connection:
        person_id = get_or_create_person(connection, person)
        if connection.execute(
            """
            SELECT 1 FROM tenancies
            WHERE apartment_id = ? AND person_id = ? AND end_date IS NULL
            """,
            (apartment["id"], person_id),
        ).fetchone():
            raise ValueError("This person already has an active tenancy in that apartment.")
        connection.execute(
            """
            INSERT INTO tenancies (apartment_id, person_id, start_date)
            VALUES (?, ?, ?)
            """,
            (apartment["id"], person_id, start_date),
        )
    print(f"Tenant added to {apartment['apartment_no']} in {apartment['tower']}.")


def end_tenancy(connection: sqlite3.Connection) -> None:
    apartment = select_apartment(connection)
    tenants = connection.execute(
        """
        SELECT tenancies.id, people.name, people.contact_no, tenancies.start_date
        FROM tenancies
        JOIN people ON people.id = tenancies.person_id
        WHERE tenancies.apartment_id = ? AND tenancies.end_date IS NULL
        ORDER BY people.name
        """,
        (apartment["id"],),
    ).fetchall()
    if not tenants:
        print("There are no active tenants in this apartment.")
        return

    for index, tenant in enumerate(tenants, start=1):
        start = tenant["start_date"] or "unknown"
        print(f"{index}. {tenant['name']} - {tenant['contact_no']} (started {start})")
    try:
        selected = int(input("Choose the tenant to move out: ").strip())
    except ValueError as exc:
        raise ValueError("Enter the number beside the tenant.") from exc
    if not 1 <= selected <= len(tenants):
        raise ValueError("That tenant selection is out of range.")

    tenant = tenants[selected - 1]
    end_date = validate_date(
        input(f"Move-out date (YYYY-MM-DD, default {date.today().isoformat()}): ").strip()
        or date.today().isoformat(),
        "Move-out date",
    )
    if tenant["start_date"] and end_date < tenant["start_date"]:
        raise ValueError("Move-out date cannot be before the tenancy start date.")
    with connection:
        connection.execute(
            "UPDATE tenancies SET end_date = ? WHERE id = ? AND end_date IS NULL",
            (end_date, tenant["id"]),
        )
    print(f"{tenant['name']}'s tenancy ended on {end_date}.")


def show_apartment(connection: sqlite3.Connection, apartment: sqlite3.Row) -> None:
    print(f"\n{apartment['tower']} - {apartment['apartment_no']}")
    owner = connection.execute(
        "SELECT name, contact_no FROM people WHERE id = ?",
        (apartment["owner_person_id"],),
    ).fetchone() if apartment["owner_person_id"] is not None else None
    print(f"Owner: {owner['name']} ({owner['contact_no']})" if owner else "Owner: not recorded")

    family = connection.execute(
        """
        SELECT people.name, people.contact_no, family_members.relation
        FROM family_members
        JOIN people ON people.id = family_members.person_id
        WHERE family_members.apartment_id = ?
        ORDER BY people.name
        """,
        (apartment["id"],),
    ).fetchall()
    print("Family members:")
    if family:
        for member in family:
            print(f"  - {member['name']} ({member['relation']}; {member['contact_no']})")
    else:
        print("  None recorded")

    tenancies = connection.execute(
        """
        SELECT people.name, people.contact_no, tenancies.start_date, tenancies.end_date
        FROM tenancies
        JOIN people ON people.id = tenancies.person_id
        WHERE tenancies.apartment_id = ?
        ORDER BY tenancies.end_date IS NOT NULL, people.name, tenancies.start_date
        """,
        (apartment["id"],),
    ).fetchall()
    print("Tenants:")
    if not tenancies:
        print("  None recorded")
    for tenant in tenancies:
        start = tenant["start_date"] or "unknown start"
        if tenant["end_date"] is None:
            print(f"  - {tenant['name']} ({tenant['contact_no']}; since {start}; current)")
        else:
            print(
                f"  - {tenant['name']} ({tenant['contact_no']}; "
                f"{start} to {tenant['end_date']}; former)"
            )


def list_apartments(connection: sqlite3.Connection) -> None:
    apartments = connection.execute(
        "SELECT id, tower, apartment_no, owner_person_id FROM apartments "
        "ORDER BY tower_key, apartment_key"
    ).fetchall()
    if not apartments:
        print("No apartments have been recorded.")
        return
    for apartment in apartments:
        show_apartment(connection, apartment)


def list_people(connection: sqlite3.Connection) -> None:
    people = connection.execute(
        """
        SELECT name, contact_no, occupation_status, work_category
        FROM people
        ORDER BY name_key, contact_key
        """
    ).fetchall()
    if not people:
        print("No people have been recorded.")
        return
    for person in people:
        status = person["occupation_status"].replace("_", " ")
        print(
            f"{person['name']} - {person['contact_no']} - {status} - "
            f"{person['work_category']}"
        )


def backup_database(
    connection: sqlite3.Connection, backup_directory: Path
) -> Path:
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_file = backup_directory / f"hst_app_{timestamp}.db"
    with open_database(backup_file) as backup_connection:
        connection.backup(backup_connection)
    print(f"Database backup saved to {backup_file}.")
    return backup_file


def find_and_show_apartment(connection: sqlite3.Connection) -> None:
    apartment = select_apartment(connection)
    show_apartment(connection, apartment)


def main() -> None:
    try:
        imported = initialize_database(DATABASE_FILE, APP_DIRECTORY)
        if imported:
            print("Imported existing JSON records into SQLite. Government ID numbers were not imported.")
        if (APP_DIRECTORY / "persons.json").exists() or (APP_DIRECTORY / "apartments.json").exists():
            print(
                "Note: legacy JSON files are unchanged and may still contain plaintext "
                "government IDs. After confirming the import, remove them securely if no "
                "longer needed."
            )

        with open_database(DATABASE_FILE) as connection:
            while True:
                print(
                    "\n1. Add apartment"
                    "\n2. Add family member"
                    "\n3. Add tenant"
                    "\n4. End tenancy"
                    "\n5. List apartments and residents"
                    "\n6. Find apartment"
                    "\n7. List people"
                    "\n8. Back up database"
                    "\n9. Exit"
                )
                choice = input("Choose an option: ").strip()
                try:
                    if choice == "1":
                        add_apartment(connection)
                    elif choice == "2":
                        add_family_member(connection)
                    elif choice == "3":
                        add_tenant(connection)
                    elif choice == "4":
                        end_tenancy(connection)
                    elif choice == "5":
                        list_apartments(connection)
                    elif choice == "6":
                        find_and_show_apartment(connection)
                    elif choice == "7":
                        list_people(connection)
                    elif choice == "8":
                        backup_database(connection, APP_DIRECTORY / "backups")
                    elif choice == "9":
                        print("Goodbye!")
                        break
                    else:
                        print("Please choose a number from 1 to 9.")
                except (ValueError, sqlite3.Error) as exc:
                    print(f"Could not complete that operation: {exc}")
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Could not start the apartment records app: {exc}")


if __name__ == "__main__":
    main()
