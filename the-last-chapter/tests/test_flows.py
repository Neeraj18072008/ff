"""
THE LAST CHAPTER — end-to-end flow tests (stdlib unittest, no extra deps).

run:  python3 tests/test_flows.py
"""
import os
import re
import secrets
import sys
import tempfile
import unittest

os.environ.setdefault("LC_INSTANCE", tempfile.mkdtemp(prefix="lc_test_"))
os.environ.setdefault("ORGANIZER_NEERAJ_PASSWORD", secrets.token_urlsafe(18))
os.environ.setdefault("ORGANIZER_ARYAN_PASSWORD", secrets.token_urlsafe(18))
os.environ.setdefault("ORGANIZER_MOHIT_PASSWORD", secrets.token_urlsafe(18))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import app, store  # noqa: E402

ORG_USER = "neeraj chouhan"
ORG_PASS = os.environ["ORGANIZER_NEERAJ_PASSWORD"]
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def csrf_from(html):
    m = CSRF_RE.search(html)
    return m.group(1) if m else ""


class Base(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.c = app.test_client()
        self.csrf = csrf_from(self.c.get("/book").get_data(as_text=True))
        self.assertTrue(self.csrf)
        conn = store.connect()
        existing = conn.execute("SELECT COUNT(*) c FROM pass_code_inventory").fetchone()["c"]
        if not existing:
            store.add_inventory_codes(conn, [f"{i:04d}" for i in range(1000)])
        conn.close()

    # ---------- helpers ----------
    def book(self, ptype="single", p1="Test User", d1="2008-01-01",
             p2="Second Person", d2="2008-02-02",
             contact="9876543210", payment="cash"):
        data = {
            "csrf_token": self.csrf, "pass_type": ptype,
            "p1_name": p1, "p1_dob": d1, "contact": contact, "payment": payment,
        }
        if ptype == "couple":
            data.update({"p2_name": p2, "p2_dob": d2})
        r = self.c.post("/book", data=data, follow_redirects=False)
        self.assertEqual(r.status_code, 302, "booking should redirect")
        return r.headers["Location"]

    def code_from(self, url):
        html = self.c.get(url).get_data(as_text=True)
        m = re.search(r'class="pc-code display">(\d{4})<', html)
        self.assertTrue(m, f"pass code not found on {url}")
        return m.group(1)

    def login(self):
        tok = csrf_from(self.c.get("/organizer/login").get_data(as_text=True))
        r = self.c.post("/organizer/login",
                        data={"csrf_token": tok, "username": ORG_USER, "password": ORG_PASS})
        self.assertEqual(r.status_code, 302)
        return csrf_from(self.c.get("/organizer").get_data(as_text=True))

    def jpost(self, url, body, csrf):
        return self.c.post(url, json=body, headers={"X-CSRF-Token": csrf})


# ---------------------------------------------------------------- public site

class TestLanding(Base):
    def test_hero_and_sections(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        for frag in ("LAST CHAPTER", "the final gathering", "3 NOVEMBER 2026",
                     "what awaits you", "the night", "SAME PEOPLE.",
                     "SAME VIBES.", "NEW JOURNEYS.", "where we meet",
                     "dress code", "the clock is running", "ready?",
                     "book your pass", "PITCHERS", "DB CITY MALL, BHOPAL"):
            self.assertIn(frag, html)
        # no memory-themed wording anywhere
        for banned in ("memories", "memory wall", "timeline of", "remember when"):
            self.assertNotIn(banned, html.lower())

    def test_healthz(self):
        self.assertEqual(self.c.get("/healthz").status_code, 200)


# ---------------------------------------------------------------- booking

class TestBooking(Base):
    def test_single_cash_flow(self):
        loc = self.book(payment="cash", p1="Neeraj Chouhan", contact="9876500001")
        self.assertIn("/confirmed/", loc)
        html = self.c.get(loc).get_data(as_text=True)
        self.assertIn("RE IN.", html)  # YOU'RE IN. (apostrophe is escaped)
        code = self.code_from(loc)
        conn = store.connect()
        b = conn.execute("SELECT * FROM booking WHERE pass_code=?", (code,)).fetchone()
        conn.close()
        self.assertEqual(b["person_1_name"], "Neeraj Chouhan")
        self.assertEqual(b["payment_method"], "cash")
        self.assertEqual(b["payment_status"], "pending")
        self.assertEqual(b["entry_status"], "unused")
        self.assertEqual(b["amount"], int(store.DEFAULT_SETTINGS["price_single"]))
        # pass page reachable
        self.assertEqual(self.c.get(f"/pass/{code}").status_code, 200)

    def test_couple_upi_flow(self):
        loc = self.book(ptype="couple", payment="upi", p1="Asha Verma",
                        p2="Rahul Verma", contact="9876500002")
        self.assertIn("/pay/", loc)
        html = self.c.get(loc).get_data(as_text=True)
        self.assertIn("<svg", html)                       # QR rendered
        self.assertIn("upi://pay", html)                  # deep link present
        self.assertIn("₹" + store.DEFAULT_SETTINGS["price_couple"], html)
        # confirm payment → submitted
        r = self.c.post(loc + "/done", data={"csrf_token": self.csrf},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        code = self.code_from(r.headers["Location"])
        conn = store.connect()
        b = conn.execute("SELECT * FROM booking WHERE pass_code=?", (code,)).fetchone()
        conn.close()
        self.assertEqual(b["payment_status"], "submitted")
        self.assertEqual(b["person_2_name"], "Rahul Verma")

    def test_validation_rejects_junk(self):
        conn_before = store.connect().execute("SELECT COUNT(*) c FROM booking").fetchone()["c"]
        r = self.c.post("/book", data={
            "csrf_token": self.csrf, "pass_type": "single",
            "p1_name": "123 <script>", "p1_dob": "2099-01-01",
            "contact": "12345", "payment": "cash"})
        self.assertEqual(r.status_code, 400)
        html = r.get_data(as_text=True)
        self.assertIn("f-err", html)
        conn = store.connect()
        after = conn.execute("SELECT COUNT(*) c FROM booking").fetchone()["c"]
        conn.close()
        self.assertEqual(after, conn_before, "no booking should be created")

    def test_missing_csrf_rejected(self):
        r = self.c.post("/book", data={"pass_type": "single", "p1_name": "A B",
                                       "p1_dob": "2008-01-01", "contact": "9876500003",
                                       "payment": "cash"})
        self.assertEqual(r.status_code, 400)

    def test_unique_codes(self):
        codes = []
        for i in range(25):
            loc = self.book(p1=f"Uni Que{chr(65 + i)}", contact=f"9876501{i:03d}")
            codes.append(self.code_from(loc))
        self.assertEqual(len(codes), len(set(codes)))

    def test_pass_not_found(self):
        self.assertEqual(self.c.get("/pass/abcd").status_code, 404)
        self.assertEqual(self.c.get("/pass/0123").status_code, 404)


# ---------------------------------------------------------------- organizer

class TestOrganizer(Base):
    def test_gate_and_login(self):
        conn = store.connect()
        usernames = {row["username"] for row in conn.execute("SELECT username FROM organizer")}
        conn.close()
        self.assertTrue(set(store.ORGANIZER_USERNAMES).issubset(usernames))
        # dashboard hidden without login
        r = self.c.get("/organizer")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/organizer/login", r.headers["Location"])
        # json endpoints blocked for logged-out users (valid csrf, no session)
        self.assertEqual(
            self.jpost("/organizer/verify", {"code": "0000"},
                       self.csrf).status_code, 401)
        # wrong password
        tok = csrf_from(self.c.get("/organizer/login").get_data(as_text=True))
        r = self.c.post("/organizer/login",
                        data={"csrf_token": tok, "username": ORG_USER,
                              "password": "nope"})
        self.assertIn("wrong username or password",
                      r.get_data(as_text=True))
        # correct login
        csrf = self.login()
        html = self.c.get("/organizer").get_data(as_text=True)
        self.assertIn("control room", html)
        self.assertIn("total bookings", html.lower())

    def test_verify_entry_duplicate_protection(self):
        csrf = self.login()
        loc = self.book(payment="cash", p1="Door Guest", contact="9876500009")
        code = self.code_from(loc)

        # verify → found, pending
        r = self.jpost("/organizer/verify", {"code": code}, csrf)
        d = r.get_json()
        self.assertEqual(d["status"], "found")
        self.assertEqual(d["pay_label"], "PENDING")

        # entry blocked while payment pending
        r = self.jpost("/organizer/entry", {"code": code}, csrf)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["status"], "payment_due")

        # mark payment received → entry allowed
        r = self.jpost("/organizer/payment", {"code": code}, csrf)
        self.assertEqual(r.get_json()["status"], "ok")
        r = self.jpost("/organizer/entry", {"code": code}, csrf)
        d = r.get_json()
        self.assertEqual(d["status"], "approved")
        self.assertEqual(d["organizer"], ORG_USER)
        self.assertRegex(d["time"], r"\d{2}:\d{2}:\d{2}")

        # second attempt → already used  (core requirement)
        r = self.jpost("/organizer/entry", {"code": code}, csrf)
        d = r.get_json()
        self.assertEqual(d["status"], "already_used")
        self.assertTrue(d["entry_time"])

        # db state + activity log
        conn = store.connect()
        b = conn.execute("SELECT * FROM booking WHERE pass_code=?", (code,)).fetchone()
        self.assertEqual(b["entry_status"], "used")
        self.assertEqual(b["verified_by"], ORG_USER)
        acts = [r_["detail"] for r_ in conn.execute(
            "SELECT detail FROM activity_log").fetchall()]
        conn.close()
        self.assertTrue(any(f"pass {code} entered" in a for a in acts))
        self.assertTrue(any("new single booking" in a for a in acts))

        # verify shows used
        r = self.jpost("/organizer/verify", {"code": code}, csrf)
        self.assertEqual(r.get_json()["entry"], "used")

    def test_verify_not_found(self):
        csrf = self.login()
        # find a code guaranteed not to exist
        conn = store.connect()
        all_codes = {r_["pass_code"] for r_ in
                     conn.execute("SELECT pass_code FROM booking").fetchall()}
        missing = next(c for c in (f"{i:04d}" for i in range(10000))
                       if c not in all_codes)
        conn.close()
        r = self.jpost("/organizer/verify", {"code": missing}, csrf)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["status"], "not_found")

    def test_bookings_search_and_filters(self):
        csrf = self.login()
        self.book(ptype="single", p1="Zeta One", contact="9876500031")
        self.book(ptype="couple", p1="Zeta Two", p2="Zeta Three",
                  contact="9876500032")
        html = self.c.get("/organizer?tab=bookings&q=zeta+one").get_data(as_text=True)
        self.assertIn("Zeta One", html)
        self.assertNotIn("Zeta Two", html)
        html = self.c.get("/organizer?tab=bookings&q=zeta&f=couple").get_data(as_text=True)
        self.assertIn("Zeta Two", html)
        self.assertIn("Zeta Three", html)
        self.assertNotIn("Zeta One</p>", html)
        for f in ("single", "couple", "cash", "upi", "paid", "pending",
                  "used", "unused"):
            self.assertEqual(
                self.c.get(f"/organizer?tab=bookings&f={f}").status_code, 200)

    def test_settings_price_change(self):
        csrf = self.login()
        r = self.c.post("/organizer/settings", data={
            "csrf_token": csrf, "price_single": "777",
            "price_couple": "1299", "upi_id_single": "queen@ybl",
            "upi_id_couple": "queen2@ybl", "payee_name": "THE LAST CHAPTER"})
        self.assertEqual(r.status_code, 302)
        try:
            loc = self.book(payment="upi", p1="Priced Guest", contact="9876500033")
            html = self.c.get(loc).get_data(as_text=True)
            self.assertIn("₹777", html)
            self.assertIn("queen@ybl", html)
        finally:
            conn = store.connect()
            conn.execute("UPDATE setting SET value=? WHERE key='price_single'",
                         (store.DEFAULT_SETTINGS["price_single"],))
            conn.commit()
            conn.close()

    def test_activity_feed(self):
        csrf = self.login()
        html = self.c.get("/organizer?tab=activity").get_data(as_text=True)
        self.assertIn("feed", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
