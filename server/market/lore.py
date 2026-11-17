"""The price lines DSM's mod adds under an item when you hover it.

Three rows, each left out when it has nothing to say:

    lbin, or the bazaar's Buy and Sell
    Med and Vol
    the full craft cost

They are written here and the mod only displays them, so the wording can change without a new jar.
Prices arrive per item; a row for a stack shows the stack's total with the single price after it.
Exact coin amounts are written in full with commas, everything else short (1.2B, 450M, 12.5K).

Written for DSM from what the lines should say. tests/test_lore.py pins every format.
"""

GRAY, DARK_GRAY, YELLOW, GOLD, AQUA, WHITE = "§7", "§8", "§e", "§6", "§b", "§f"
UNAVAILABLE_LINE = 10 ** 17            # a base at or past this cannot be made; engine.UNAVAILABLE is 10**18

# (threshold, divisor, suffix, inclusive). Checked top down; the first that fits wins. M starts
# just under a million so 999,999.5 reads 1M rather than 1000K.
SCALES = ((1_000_000_000, 1_000_000_000, "B", True),
          (999_999, 1_000_000, "M", False),
          (999, 1_000, "K", False))


def trim(value, places):
    """At most `places` decimals, with trailing zeros and a bare point dropped: 2.50 -> 2.5, 3.0 -> 3."""
    text = f"{value:.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


upto = trim     # the name the tests and older callers use


def number(price):
    """An exact amount: whole coins with commas, or one decimal below a thousand."""
    places = 1 if price < 1_000 else 0
    return f"{price:,.{places}f}"


def short(amount):
    """A compact amount with a B, M or K suffix and at most two decimals."""
    if amount == 0:
        return "0"
    sign, size = ("-", -amount) if amount < 0 else ("", amount)
    for threshold, divisor, suffix, inclusive in SCALES:
        if size > threshold or (inclusive and size == threshold):
            return sign + trim(size / divisor, 2) + suffix
    return sign + trim(size, 2)


def per_stack(fmt, unit, count):
    """`fmt` of the stack's total, plus the single price when there is more than one."""
    total = fmt(unit * count)
    return (total, f"({fmt(unit)} each)") if count > 1 else (total, "")


# ---------------------------------------------------------------- one field per function

def lbin_field(est, count):
    unit = est.get("lbin") or 0
    if not unit:
        return ""
    total, each = per_stack(number, unit, count)
    if est.get("lbin_key") == est.get("item_key"):
        return f"{GRAY}lbin: {YELLOW}{total} {each}"
    # Not this exact copy: one with more on it, or the closest kind there is.
    note = "(higher value found)" if (est.get("lbin_key") or "").endswith("+HV") else "(estimate, no match found)"
    return f"{GRAY}lbin: ~{total} {DARK_GRAY}{note}{each}"


def bazaar_field(label, unit, count):
    total, each = per_stack(short, unit, count)
    return f"{GRAY}{label}: {GOLD}{total} {each}" if each else f"{GRAY}{label}: {GOLD}{total} "


def median_field(est, count):
    """Med. An exact copy shows its own group's median. Anything else shows DSM's estimate for
    this copy, marked "~": the nearest group's median ignores what this copy has on top, and read
    266k for Tarantula Boots with 10 Hot Potato Books beside a 299m lbin. A bazaar product has no
    Med, since Buy and Sell already are its price."""
    unit, key = est.get("median") or 0, est.get("median_key")
    if not unit or key is None:
        return ""
    if "bz_buy" in est and unit == est.get("bz_price"):
        return ""
    exact = key.replace("&comb", "") == est.get("item_key")
    if not exact:
        unit = est.get("value") or unit
    total, each = per_stack(number, unit, count)
    mark = "" if exact else "~"
    return f"{GRAY}Med: {AQUA}{mark}{total} {each}"


def volume_field(est, daily):
    """Vol. A bazaar product shows units sold and bought a day; anything else sales a day."""
    if daily and daily[0] > 0:
        sold, bought = daily
        return f"{GRAY}Vol: {YELLOW}{short(sold)}{GRAY}/{YELLOW}{short(bought)}"
    if est.get("median_key") is None:
        return ""
    return f"{GRAY}Vol: {YELLOW}{trim(est.get('volume') or 0, 1)} "


def craft_field(est, count, craft):
    """What it takes to build this copy: the base (made or bought clean, whichever is cheaper)
    plus everything on it. Never more than this exact copy's own median, since buying it is
    always an option."""
    base, everything, craftable = craft
    if est.get("median_key") == est.get("item_key"):
        ceiling = (est.get("median") or 0) * count
        if ceiling > 0:
            base, everything = min(base, ceiling), min(everything, ceiling)
    if not base:
        return f"{GRAY}No Craft Cost found"
    if base >= UNAVAILABLE_LINE:
        return f"{GRAY}Craft ingredients unavailable"
    if craftable:
        return f"{GRAY}Full Craft Cost: {YELLOW}{short(everything)} "
    return f"{GRAY}Obtain cost: {WHITE}{short(everything)}{GRAY}(not craftable)"


# ---------------------------------------------------------------- assembling the lines

def join(fields):
    """One row from its fields: a space between two fields unless the first already ends in one."""
    out = ""
    for field in filter(None, fields):
        if out and not out.endswith(" "):
            out += " "
        out += field
    return out


def describe(est, count, craft, daily):
    """The lines for one item, as legacy colour-coded strings, empty rows dropped.
    `craft` is Engine.full_craft's (base, everything, craftable); `daily` is Bazaar.daily's pair."""
    count = max(int(count or 1), 1)
    if "bz_buy" in est:
        top = (lbin_field(est, count), bazaar_field("Buy", est["bz_buy"], count), bazaar_field("Sell", est["bz_sell"], count))
    else:
        top = (lbin_field(est, count),)
    rows = (top, (median_field(est, count), volume_field(est, daily)), (craft_field(est, count, craft),))
    return [line for line in (join(row) for row in rows) if line]
