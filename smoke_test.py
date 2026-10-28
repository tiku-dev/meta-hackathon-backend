"""Live end-to-end smoke test of the 5-minute demo flow (blueprint SS12).

Run the server first:  py -3.14 manage.py runserver 127.0.0.1:8017
Then:                  py -3.14 smoke_test.py
"""
import requests

BASE = "http://127.0.0.1:8017/api"
PW = "DemoPass123!"


def login(email):
    r = requests.post(f"{BASE}/auth/token/", json={"email": email, "password": PW})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access']}"}


def main():
    admin = login("admin@demo.edu")
    student = login("student@demo.edu")
    lecturer = login("lecturer@demo.edu")

    overview = requests.get(f"{BASE}/admin/overview", headers=admin).json()
    print("overview:", {k: overview[k] for k in
          ("students_pending_activation", "unpaid_invoices", "registrations")})

    me = requests.get(f"{BASE}/student/me", headers=student).json()
    sid = me["student"]["id"]
    print("student:", me["student"]["matricule"], "| due:", me["total_due"])

    # registration blocked before payment
    r = requests.get(f"{BASE}/student/registration/available", headers=student)
    data = r.json()
    print("eligible before payment:", data["eligible"],
          "| reasons:", [x["code"] for x in data["reasons"]])
    offering_id = data["offerings"][0]["id"]

    fees = requests.get(f"{BASE}/student/fees", headers=student).json()
    invoice_id = fees["current_invoice"]["id"]
    pay = requests.post(f"{BASE}/invoices/{invoice_id}/payments",
                        json={}, headers=student).json()
    print("payment attempt:", pay["reference"], pay["status"])

    r = requests.post(f"{BASE}/payments/{pay['reference']}/mock-simulate",
                      json={"outcome": "succeeded"}, headers=student)
    print("after provider confirm:", r.json()["status"])

    r = requests.post(f"{BASE}/student/registrations",
                      json={"offering_ids": [offering_id]}, headers=student)
    print("registration:", r.status_code, r.json()[0]["course_code"]
          if r.status_code == 201 else r.json())
    registration_id = r.json()[0]["id"]

    r = requests.put(f"{BASE}/lecturer/offerings/{offering_id}/results",
                     json={"submit": True, "marks": [
                         {"registration_id": registration_id,
                          "ca_mark": "24", "final_mark": "56"}]},
                     headers=lecturer)
    print("marks:", r.json()["saved"][0]["total"],
          r.json()["saved"][0]["grade_letter"])

    registrar = login("registrar@demo.edu")
    r = requests.post(f"{BASE}/admin/offerings/{offering_id}/publish-results",
                      json={}, headers=registrar)
    print("published:", r.json())

    r = requests.get(f"{BASE}/student/results", headers=student)
    print("student results:", [(x["course_code"], x["total"], x["grade_letter"])
                               for x in r.json()["results"]], "| GPA:", r.json()["gpa"])
    print("SMOKE OK")


if __name__ == "__main__":
    main()
