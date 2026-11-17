"""A running tape of bazaar trades, worked out by watching the order book.

Dragsim never says who traded or at what price, but every 30 seconds the feed gives the whole book
and a count of items traded. Between two looks, three things can be told apart:

  - A level loses items and keeps the same number of orders. A cancel removes a whole order, so
    this can only be somebody filling one bit by bit. Fear's idea, and it holds: on 6,532 saved
    cases the traded count rose on 6,500 of them, 99.5%.
  - A level disappears. On its own that proves nothing: 4,102 were real and 3,048 were cancels.
  - The traded count, which counts items exactly. It rose by precisely what left the book 5,651
    times even with books five minutes apart. So a vanished order that moved the count was
    bought, and one that did not was pulled.

Each fill is stamped with how it is known:

    exact    everything that left the book adds up to the rise in the count
    serial   a level filled part way, the near-certain case, with other shrinking that does not add up
    likely   the count rose by less than left the book, so these are the best-placed levels up to
             that amount, since buyers take the cheapest offer and sellers the highest order first
    unseen   the count rose by more than left the book: bought and refilled between two looks, so the
             price is unknown. Kept for volume, never used for a price.

`side` is who crossed the spread: "buy" took a sell offer, "sell" hit a buy order.
"""
import collections

MAX_GAP = 5 * 60_000            # past this a hole in the feed can hide anything, so nothing is claimed


class Fill:
    __slots__ = ("ts", "product", "side", "price", "amount", "proof", "orders")

    def __init__(self, ts, product, side, price, amount, proof, orders=0):
        self.ts = ts
        self.product = product
        self.side = side
        self.price = price
        self.amount = amount
        self.proof = proof
        self.orders = orders

    def row(self):
        return (self.ts, self.product, self.side, self.price, self.amount, self.proof, self.orders)


def levels(rows):
    return {l["price"]: (l["amount"], l.get("orders", 1)) for l in rows or ()}


def best_first(side):
    """Buyers take the cheapest offer first, sellers the highest order first."""
    return (lambda c: c[0]) if side == "buy" else (lambda c: -c[0])


def losses(before, after, side):
    """(price, lost, orders, serial) for every level that lost items and could have been eaten.
    A level further out than today's best that shrank was pulled, because the better price would
    have had to go first."""
    if after:
        edge = min(after) if side == "buy" else max(after)
    else:
        edge = float("inf") if side == "buy" else 0
    out = []
    for price, (amount, orders) in before.items():
        now_amount, now_orders = after.get(price, (0, 0))
        lost = amount - now_amount
        if lost <= 0:
            continue
        if (side == "buy" and price > edge) or (side == "sell" and price < edge):
            continue
        out.append((price, lost, orders, now_amount > 0 and now_orders == orders))
    return out


def read(ts, product, before_buys, before_sells, buys, sells, rose):
    """Everything that traded between two looks at one product."""
    if rose <= 0:
        return []
    candidates = [("buy", c) for c in sorted(losses(before_sells, sells, "buy"), key=best_first("buy"))]
    candidates += [("sell", c) for c in sorted(losses(before_buys, buys, "sell"), key=best_first("sell"))]
    total = sum(c[1] for _, c in candidates)
    fills = []
    if total == rose:
        for side, (price, lost, orders, _) in candidates:
            fills.append(Fill(ts, product, side, price, lost, "exact", orders))
        return fills
    left = rose
    # Serial levels first: they are the ones that are almost certainly real.
    for side, (price, lost, orders, serial) in sorted(candidates, key=lambda row: not row[1][3]):
        if left <= 0:
            break
        amount = min(lost, left)
        left -= amount
        fills.append(Fill(ts, product, side, price, amount, "serial" if serial else "likely", orders))
    if left > 0:
        fills.append(Fill(ts, product, "unknown", 0, left, "unseen"))
    return fills


class Tape:
    """Holds the last look at each product and turns each new look into fills."""

    def __init__(self):
        self.last = {}
        self.at = 0
        self.tally = collections.Counter()

    def feed(self, ts, products):
        """`products` as the market feed gives them. Returns the fills since the last look."""
        if ts <= self.at:
            return []
        fills = []
        for p in products:
            name = p.get("id")
            traded = p.get("traded")
            buys, sells = levels(p.get("buys")), levels(p.get("sells"))
            previous = self.last.get(name)
            self.last[name] = (ts, traded, buys, sells)
            if not previous or traded is None or previous[1] is None:
                continue
            if ts - previous[0] > MAX_GAP:
                self.tally["gap"] += 1
                continue
            rose = traded - previous[1]
            if rose <= 0:
                continue
            found = read(ts, name, previous[2], previous[3], buys, sells, rose)
            for fill in found:
                self.tally[fill.proof] += fill.amount
            fills.extend(found)
        self.at = ts
        return fills


def summary(fills, now, window):
    """What a product did over a window, from its fills."""
    since = now - window
    bought = [f for f in fills if f.ts >= since and f.side == "buy"]
    sold = [f for f in fills if f.ts >= since and f.side == "sell"]
    unseen = sum(f.amount for f in fills if f.ts >= since and f.proof == "unseen")

    def side(rows):
        amount = sum(f.amount for f in rows)
        return {"amount": amount, "average": sum(f.price * f.amount for f in rows) / amount if amount else 0,
                "coins": sum(f.price * f.amount for f in rows)}

    return {"bought": side(bought), "sold": side(sold), "unseen": unseen}
