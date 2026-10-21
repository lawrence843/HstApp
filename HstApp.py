import json
from pathlib import Path
from typing import Any


class Person:
    def __init__(
        self,
        name: str,
        contact_no: str,
        occupation_status: str,
        work_category: str,
        govt_id: dict[str, str],
    ) -> None:
        self.name: str = name
        self.contact_no: str = contact_no
        self.occupation_status: str = occupation_status
        self.work_category: str = work_category
        self.govt_id: dict[str, str] = govt_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "contact_no": self.contact_no,
            "occupation_status": self.occupation_status,
            "work_category": self.work_category,
            "govt_id": self.govt_id,
        }


def load_json_list(file_path: Path) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    if isinstance(data, list):
        return data
    return []


def save_json_list(file_path: Path, data: list[dict[str, Any]]) -> None:
    file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def save_person(person: Person) -> None:
    people_file = Path(__file__).resolve().with_name("persons.json")
    people = load_json_list(people_file)
    people.append(person.to_dict())
    save_json_list(people_file, people)


GOVT_ID_TYPES: dict[str, str] = {"1": "Aadhar_id", "2": "Passport_no", "3": "Driving_Licence"}
JOB_STATUS_TYPES: dict[str, str] = {"1": "Working", "2": "Not_Working", "3": "Retired", "4": "House_Wife", "5": "Student"}


def validate_text(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty.")
    return cleaned


def collect_govt_id() -> dict[str, str]:
    while True:
        print("1. Aadhar ID")
        print("2. Passport")
        print("3. Driving Licence")

        choice = input("Select an ID type (1, 2, or 3): ").strip()
        id_type = GOVT_ID_TYPES.get(choice)

        if id_type is None:
            print("Please enter 1, 2, or 3.")
            continue

        try:
            id_number = validate_text(input(f"Enter your {id_type} Number: "), f"{id_type} number")
        except ValueError as exc:
            print(exc)
            continue

        return {id_type: id_number}


def collect_job_status() -> str:
    while True:
        print("1. Working")
        print("2. Not Working")
        print("3. Retired")
        print("4. House Wife")
        print("5. Student")

        choice = input("Enter 1-5 or a status name: ").strip()
        if not choice:
            print("Please enter a valid selection.")
            continue

        if choice in JOB_STATUS_TYPES:
            return JOB_STATUS_TYPES[choice]

        normalized_choice = choice.lower().replace("_", " ")
        for value in JOB_STATUS_TYPES.values():
            if normalized_choice == value.lower().replace("_", " "):
                return value

        print("Please enter 1-5 or one of the listed status names.")


class Apartment:
    def __init__(self, apartment_no: str, tower: str, owner: Person | None = None) -> None:
        self.apartment_no: str = apartment_no
        self.tower: str = tower
        self.owner: Person | None = owner
        self.family_members: list[dict[str, Any]] = []
        self.tenants: list[Person] = []

    def add_family_member(self, person: Person, relation: str) -> None:
        self.family_members.append({"person": person, "relation": relation})

    def add_tenants(self, person: Person) -> None:
        self.tenants.append(person)

    def to_dict(self) -> dict[str, Any]:
        return {
            "apartment_no": self.apartment_no,
            "tower": self.tower,
            "owner": self.owner.to_dict() if self.owner else None,
            "family_members": [
                {
                    "person": member["person"].to_dict(),
                    "relation": member["relation"],
                }
                for member in self.family_members
            ],
            "tenants": [tenant.to_dict() for tenant in self.tenants],
        }


class ResidentialAssociation:
    def __init__(self, name: str) -> None:
        self.name: str = name
        self.apartments: dict[str, Apartment] = {}

    def add_apartment(self, apartment: Apartment) -> None:
        self.apartments[apartment.apartment_no] = apartment

    def get_apartment(self, apartment_no: str) -> Apartment | None:
        return self.apartments.get(apartment_no)


def collect_person_input() -> Person:
    name = validate_text(input("Enter person name: "), "Name")
    contact_no = validate_text(input("Enter contact number: "), "Contact number")
    occupation_status = collect_job_status()
    work_category = validate_text(input("Enter work category: "), "Work category")
    govt_id = collect_govt_id()

    return Person(
        name=name,
        contact_no=contact_no,
        occupation_status=occupation_status,
        work_category=work_category,
        govt_id=govt_id,
    )


def collect_apartment_input(owner: Person | None = None) -> Apartment:
    apartment_no = validate_text(input("Enter apartment number: "), "Apartment number")
    tower = validate_text(input("Enter tower name: "), "Tower")
    return Apartment(apartment_no=apartment_no, tower=tower, owner=owner)


def save_apartment(apartment: Apartment) -> None:
    apartments_file = Path(__file__).resolve().with_name("apartments.json")
    apartments = load_json_list(apartments_file)
    apartments.append(apartment.to_dict())
    save_json_list(apartments_file, apartments)


def main() -> None:
    while True:
        print("\n1. Add person and apartment")
        print("2. Show saved people")
        print("3. Exit")

        choice = input("Choose an option: ").strip()

        if choice == "1":
            person = collect_person_input()
            apartment = collect_apartment_input(person)
            apartment.add_family_member(person, "Owner")

            save_person(person)
            save_apartment(apartment)

            print("Person and apartment saved successfully.")

        elif choice == "2":
            people_file = Path(__file__).resolve().with_name("persons.json")
            records = load_json_list(people_file)
            if not records:
                print("No saved people found.")
            else:
                for record in records:
                    print(record)

        elif choice == "3":
            print("Goodbye!")
            break

        else:
            print("Please choose 1, 2, or 3.")


if __name__ == "__main__":
    main()
