"""Fake but plausible field values, without third-party dependencies. Every generator takes a ``random.Random`` so a run can be
replayed (``--seed``). Values are picked by field *type* first, then refined by field *name* (``email``, ``phone``, ``qty``…)."""
from __future__ import annotations

import datetime as dt
import decimal
import uuid

from django.core import validators as dj_validators
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

FIRST = ["Ayesha", "Rahim", "Karim", "Nadia", "Tanvir", "Farhana", "Imran", "Sadia", "Hasan", "Mitu", "Arif", "Sumaiya", "Rafiq",
         "Lamia", "Jamal", "Tania", "Omar", "Rina", "Sabbir", "Nusrat", "John", "Maria", "Chen", "Priya", "Lukas", "Sofia"]
LAST = ["Ahmed", "Hossain", "Rahman", "Islam", "Khan", "Chowdhury", "Begum", "Akter", "Uddin", "Sarkar", "Das", "Roy", "Smith",
        "Garcia", "Wang", "Patel", "Muller", "Rossi"]
ORG_A = ["Northern", "Delta", "Prime", "Green", "Blue Ocean", "Sunrise", "Royal", "Eastern", "Metro", "Golden", "Apex", "Silver"]
ORG_B = ["Knit", "Textiles", "Apparels", "Fashion", "Garments", "Trading", "Sourcing", "Fabrics", "Dyeing", "Composite", "Exports"]
ORG_C = ["Ltd.", "Limited", "Inc.", "Co.", "Group", "LLC"]
ADJ = ["Classic", "Premium", "Soft", "Heavy", "Light", "Organic", "Combed", "Slub", "Brushed", "Striped", "Plain", "Melange",
       "Stretch", "Eco", "Basic", "Summer", "Winter"]
NOUN = ["Jersey", "Rib", "Interlock", "Fleece", "Pique", "Polo", "Tee", "Hoodie", "Jogger", "Cardigan", "Sweater", "Tank",
        "Dress", "Shirt", "Legging", "Jacket", "Cap", "Scarf"]
COLORS = ["Black", "White", "Navy", "Red", "Olive", "Grey Melange", "Sky Blue", "Maroon", "Beige", "Charcoal", "Mint", "Mustard"]
SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
CITIES = ["Dhaka", "Gazipur", "Narayanganj", "Chattogram", "Savar", "Ashulia", "Mymensingh", "Comilla", "Khulna", "Sylhet"]
COUNTRIES = ["Bangladesh", "Germany", "Spain", "United Kingdom", "United States", "Sweden", "Netherlands", "France", "Italy", "Japan"]
STREETS = ["Road", "Avenue", "Lane", "Street", "Highway"]
WORDS = ("quality check shipment sample lab dip approval delivery order fabric yarn knitting dyeing finishing cutting sewing "
         "packing inspection buyer merchandiser production schedule invoice payment").split()

COMPANYISH = ("company", "buyer", "customer", "client", "supplier", "vendor", "party", "factory", "brand", "agent", "bank")
PERSONISH = ("person", "employee", "worker", "operator", "contact", "staff", "member")
CODE_NAMES = ("code", "ref", "reference", "number", "sku", "barcode", "tax_id", "official_id", "voucher_no", "invoice_no")


def person(rng) -> tuple[str, str]:
    return rng.choice(FIRST), rng.choice(LAST)


def full_name(rng) -> str:
    return " ".join(person(rng))


def company_name(rng) -> str:
    return f"{rng.choice(ORG_A)} {rng.choice(ORG_B)} {rng.choice(ORG_C)}"


def product_name(rng) -> str:
    return f"{rng.choice(ADJ)} {rng.choice(NOUN)}"


def sentence(rng, words: int = 0) -> str:
    text = " ".join(rng.choice(WORDS) for _ in range(words or rng.randint(5, 12)))
    return text[0].upper() + text[1:] + "."


def paragraph(rng) -> str:
    return " ".join(sentence(rng) for _ in range(rng.randint(2, 4)))


def address(rng) -> str:
    return f"House {rng.randint(1, 250)}, {rng.choice(LAST)} {rng.choice(STREETS)}, {rng.choice(CITIES)}"


def phone(rng) -> str:
    return f"+8801{rng.randint(3, 9)}{rng.randint(10000000, 99999999)}"


def prefix_for(model) -> str:
    letters = "".join(w[0] for w in str(model._meta.verbose_name).split() if w)[:3].upper()
    return letters or "REC"


def _bounds(field, low, high):
    for v in field.validators:
        if isinstance(v, dj_validators.MinValueValidator):
            low = max(low, v.limit_value)
        elif isinstance(v, dj_validators.MaxValueValidator):
            high = min(high, v.limit_value)
    return low, high


def _clip(field, value: str) -> str:
    limit = getattr(field, "max_length", None)
    return value[:limit] if limit else value


def text_for(field, rng, model, n: int) -> str:
    """A string for a Char/Text field, guessed from the field and model names. ``n`` keeps codes/e-mails unique."""
    name, mname = field.name.lower(), model._meta.model_name.lower()
    if isinstance(field, models.EmailField) or "email" in name:
        first, last = person(rng)
        return _clip(field, f"{slugify(first)}.{slugify(last)}{n}@example.com")
    if isinstance(field, models.URLField) or name in ("url", "website", "web", "link"):
        return _clip(field, f"https://example.com/{slugify(mname)}/{n}")
    if isinstance(field, models.SlugField) or name == "slug":
        return _clip(field, f"{slugify(mname)}-{n}")
    if any(k in name for k in ("phone", "mobile", "fax", "contact_no", "tel")):
        return _clip(field, phone(rng))
    if any(k in name for k in ("address", "location")):
        return _clip(field, address(rng))
    if "city" in name:
        return _clip(field, rng.choice(CITIES))
    if "country" in name:
        return _clip(field, rng.choice(COUNTRIES))
    if "colo" in name:
        return _clip(field, rng.choice(COLORS))
    if "size" in name:
        return _clip(field, rng.choice(SIZES))
    if name in ("full_name", "contact_person") or name.endswith("_person") or (name == "name" and any(k in mname for k in PERSONISH)):
        return _clip(field, full_name(rng))
    if name == "first_name":
        return _clip(field, person(rng)[0])
    if name == "last_name":
        return _clip(field, person(rng)[1])
    if name in ("abbreviation", "short_name", "initials"):
        return _clip(field, f"{prefix_for(model)}{n}")
    if name in CODE_NAMES or name.endswith(("_no", "_code", "_ref", "_number")):
        return _clip(field, f"{prefix_for(model)}-{n:05d}")
    if isinstance(field, models.TextField):
        return _clip(field, paragraph(rng))
    if any(k in name for k in ("description", "remark", "note", "comment", "detail", "body", "terms")):
        return _clip(field, sentence(rng))
    if "name" in name or name in ("title", "label", "subject"):
        if any(k in mname for k in COMPANYISH) or "legal" in name:
            return _clip(field, company_name(rng))
        return _clip(field, f"{product_name(rng)} {n}")
    return _clip(field, sentence(rng, 3).rstrip("."))


def number_for(field, rng, n: int):
    name = field.name.lower()
    if isinstance(field, (models.DecimalField, models.FloatField)):
        low, high = (1, 1000) if any(k in name for k in ("price", "rate", "amount", "cost", "value", "total")) else (0, 100)
        if any(k in name for k in ("percent", "pct", "discount", "tax")):
            low, high = 0, 30
        if isinstance(field, models.DecimalField):
            high = min(high, 10 ** (field.max_digits - field.decimal_places) - 1)
            low, high = _bounds(field, low, high)
            step = decimal.Decimal(1).scaleb(-field.decimal_places)
            return decimal.Decimal(str(rng.uniform(float(low), float(high)))).quantize(step, rounding=decimal.ROUND_DOWN)
        low, high = _bounds(field, low, high)
        return round(rng.uniform(low, high), 2)
    if any(k in name for k in ("position", "order", "sequence", "sort", "index")):
        return n
    low, high = (1, 500) if any(k in name for k in ("qty", "quantity", "count", "pcs", "pieces")) else (0, 100)
    low, high = _bounds(field, low, high)
    return rng.randint(int(low), int(max(low, high)))


def value_for(field, rng, model, n: int):
    """A value for a concrete, non-relational field, or ``NotImplemented`` when it cannot be faked (files, binary…)."""
    if field.choices:
        keys = [k for k, _ in field.flatchoices if k not in ("", None)]
        return rng.choice(keys) if keys else NotImplemented
    if isinstance(field, models.BooleanField):
        return rng.random() < (0.85 if field.name in ("is_active", "active") else 0.5)
    if isinstance(field, models.GenericIPAddressField):
        return f"10.0.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
    if isinstance(field, (models.CharField, models.TextField)):
        return text_for(field, rng, model, n)
    if isinstance(field, (models.IntegerField, models.DecimalField, models.FloatField)):
        return number_for(field, rng, n)
    if isinstance(field, models.DateTimeField):
        return timezone.now() - dt.timedelta(days=rng.randint(-60, 180), minutes=rng.randint(0, 1440))
    if isinstance(field, models.DateField):
        return timezone.localdate() - dt.timedelta(days=rng.randint(-60, 180))
    if isinstance(field, models.TimeField):
        return dt.time(rng.randint(8, 18), rng.choice((0, 15, 30, 45)))
    if isinstance(field, models.DurationField):
        return dt.timedelta(hours=rng.randint(1, 72))
    if isinstance(field, models.UUIDField):
        return uuid.UUID(int=rng.getrandbits(128), version=4)
    if isinstance(field, models.JSONField):
        return field.get_default() if field.has_default() else {}
    return NotImplemented
