from atlas.agents import Ctx
from atlas.agents.intake import Intake
from atlas.config import Config
from atlas.importers import from_whatsapp, parse_contacts
from atlas.store import Store
from test_swarm import Clock

GOOGLE = """First Name,Middle Name,Last Name,Phonetic First Name,Nickname,File As,Birthday,Notes,Photo,Labels,E-mail 1 - Label,E-mail 1 - Value,Phone 1 - Label,Phone 1 - Value,Phone 2 - Label,Phone 2 - Value,Organization Name,Organization Title
Aisha,,Rahman,,Kak Aisha,,,Met at Penang Rotary 2016,https://lh3.googleusercontent.com/x,* myContacts ::: Rotary,Home,aisha@x.my,Mobile,+60 12-345 6789 ::: 04-1234567,,,Kedai Aisha,Owner
Tan,,Wei Ming,,,,,,,* myContacts,,,Work,,Mobile,016-555 1234,Tan Florist,
No Phone,,Person,,,,,,,,,,,,,,,
"""

ANDROID_VCF = """BEGIN:VCARD
VERSION:2.1
N;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:;=E9=99=88=E5=A4=A7=E6=96=87;;;
FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=E9=99=88=E5=A4=A7=E6=96=87
TEL;HOME:04-1234567
TEL;CELL:012-888 9999
NOTE:Old colleague from Intel\\, 2012
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Siti Nur
N:Nur;Siti;;;
item1.TEL;type=CELL:+60198887777
ORG:Siti Spa;
TITLE:Founder
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Folded
TEL;TYPE=CELL:0123
 456780
END:VCARD
"""


def test_google_contacts_export():
    rows = parse_contacts(GOOGLE)
    assert [r["name"] for r in rows] == ["Aisha Rahman", "Tan Wei Ming"]     # no phone: dropped
    a, t = rows
    assert a["phone"] == "+60 12-345 6789" and a["company"] == "Kedai Aisha"
    assert a["facts"]["notes"] == "Met at Penang Rotary 2016"
    assert a["facts"]["labels"] == "Rotary" and a["facts"]["nickname"] == "Kak Aisha"
    assert a["facts"]["organization_title"] == "Owner"
    assert not any("photo" in k or "label" in k and k != "labels" for k in a["facts"])
    assert t["phone"] == "016-555 1234"                                     # found in Phone 2


def test_android_and_iphone_vcards():
    rows = parse_contacts(ANDROID_VCF)
    assert [r["name"] for r in rows] == ["陈大文", "Siti Nur", "Folded"]
    assert rows[0]["phone"] == "012-888 9999"                               # mobile preferred over home
    assert rows[0]["facts"]["notes"] == "Old colleague from Intel, 2012"
    assert rows[1]["company"] == "Siti Spa" and rows[1]["facts"]["job_title"] == "Founder"
    assert rows[2]["phone"] == "0123456780"                                 # folded line rejoined


def test_hand_typed_spreadsheet_with_semicolons_and_malay_headers():
    rows = parse_contacts("Nama;No Tel;Syarikat;How we know\nAhmad;012-1112222;Ahmad Motor;school friend\n")
    assert rows == [{"name": "Ahmad", "phone": "012-1112222", "company": "Ahmad Motor",
                     "facts": {"how_we_know": "school friend"}}]


def test_whatsapp_contacts_become_rows():
    rows = from_whatsapp([
        {"phone": "+60123456789", "name": "Aisha Bakery", "notify": "Aisha", "last_chat": 1561939200},
        {"phone": "+60111111111", "name": "", "notify": "", "last_chat": 0},     # nameless: skipped
    ])
    assert rows == [{"name": "Aisha Bakery", "phone": "+60123456789", "company": "",
                     "facts": {"whatsapp_name": "Aisha", "last_chatted": "July 2019"}}]


def test_import_lands_on_the_chosen_phone_and_list_and_dedupes_across_phones():
    ctx = Ctx(Store(), Config(), None, Clock())
    r1 = Intake().run_text(ctx, GOOGLE, account="2", list_name="Rotary")
    r2 = Intake().run_text(ctx, ANDROID_VCF + "\n", account="3", list_name="Old phone")
    assert r1.done == 2 and r2.done == 3
    again = Intake().run_text(ctx, GOOGLE, account="1", list_name="dup")
    assert again.done == 0 and again.skipped == {"duplicate": 2}
    a = ctx.store.lead(1)
    assert (a.account, a.list_name, a.phone) == ("2", "Rotary", "+60123456789")
    assert {(l["account"], l["list_name"], l["n"]) for l in ctx.store.lists()} == {("2", "Rotary", 2), ("3", "Old phone", 3)}
