"""What bazaar products cost, from the order book.

Dragsim publishes how many of a product traded but never at what price. A market with published
averages could take the middle of the spread. Here that fails: buy orders sit at
1 coin beside 500m troll offers, and Recombobulator's only order is 1m against 600m offers.

So a sale is read from the mark it leaves. When the traded count rises and the best level
shrinks, that level is where the sale happened. An order being pulled shrinks a level too,
which is why nothing counts unless the count rose, and never for more than it rose by.

The idea is from the collector's bazaar_sales.py. This one runs incrementally so the
engine can replay history and stay live with the same code.
"""
import collections
import json

WINDOW = 7 * 86_400_000
MAX_GAP = 15 * 60_000           # a longer hole in the feed can hide a level emptying and refilling
MIN_ITEMS = 100                 # below this it is one seller's dump, not a price
MIN_SAMPLES = 10
THIN_SAMPLES = 3                # enough to prefer real fills over a one-sided book


class Book:
    __slots__ = ("last", "fills", "sold", "samples", "bid", "ask", "bid_qty", "ask_qty", "traded", "at", "asks", "_cached",
                 "_cached_at", "_daily", "_daily_at")

    def __init__(self):
        self.last = None
        self.fills = collections.deque()    # (ts, price, amount)
        self.sold = collections.deque()     # the same, for sellers hitting buy orders, which is what fills a new one
        self.samples = collections.deque()  # ts of each sample that saw a sale
        self.bid = self.ask = 0
        self.bid_qty = self.ask_qty = 0
        self.traded = 0
        self.at = 0
        self.asks = collections.deque(maxlen=288)
        self._cached = None
        self._cached_at = -1
        self._daily = (0, 0)
        self._daily_at = -1


class Bazaar:
    def __init__(self, npc=None):
        self.books = {}
        self.npc = npc or (lambda entry: 0)
        # Once the tape is running its fills replace these five minute guesses. Both at once would
        # count every trade twice.
        self.tape_from = None

    def add_fill(self, ts, product, price, amount, side=None):
        """A fill read off the 30 second tape. An unseen one has no price and is left out."""
        if not price:
            return
        book = self.books.get(product)
        if book is None:
            book = self.books[product] = Book()
        book.fills.append((ts, price, amount))
        if side == "sell":
            book.sold.append((ts, price, amount))
        if not book.samples or book.samples[-1] != ts:
            book.samples.append(ts)
        self._trim(book, ts)
        book._cached_at = book._daily_at = -1

    @staticmethod
    def _trim(book, ts):
        cutoff = ts - WINDOW
        for rows in (book.fills, book.sold, book.samples):
            while rows and (rows[0][0] if isinstance(rows[0], tuple) else rows[0]) < cutoff:
                rows.popleft()

    def update(self, ts, product, bid, ask, buy_qty, sell_qty, traded, book_json):
        book = self.books.get(product)
        if book is None:
            book = self.books[product] = Book()
        book.bid, book.ask, book.bid_qty, book.ask_qty, book.traded, book.at = bid or 0, ask or 0, buy_qty, sell_qty, traded, ts
        if not book_json:
            return
        parsed = json.loads(book_json) if isinstance(book_json, str) else book_json
        buys = {l["price"]: l["amount"] for l in parsed.get("buys", [])}
        sells = {l["price"]: l["amount"] for l in parsed.get("sells", [])}
        if sells:
            book.asks.append(min(sells))
        last = book.last
        taped = self.tape_from is not None and ts >= self.tape_from
        if not taped and last and traded is not None and last[1] is not None and ts - last[0] < MAX_GAP and traded > last[1]:
            # Only levels at or past today's best can have been eaten. One further out that
            # shrank was pulled, since a buyer takes the cheapest offer first.
            top = min(sells) if sells else float("inf")
            bought = [(p, n - sells.get(p, 0)) for p, n in last[3].items() if p <= top and sells.get(p, 0) < n]
            top = max(buys) if buys else 0
            hit = [(p, n - buys.get(p, 0)) for p, n in last[2].items() if p >= top and buys.get(p, 0) < n]
            total = sum(n for _, n in bought + hit)
            if total:
                scale = min(1, (traded - last[1]) / total)
                book.samples.append(ts)
                for p, n in bought + hit:
                    book.fills.append((ts, p, n * scale))
                for p, n in hit:
                    book.sold.append((ts, p, n * scale))
        book.last = (ts, traded, buys, sells)
        self._trim(book, ts)
        book._cached_at = book._daily_at = -1

    def quote(self, entry):
        book = self.books.get(entry)
        if not book:
            return None
        return {"bid": book.bid, "ask": book.ask, "bid_qty": book.bid_qty, "ask_qty": book.ask_qty, "at": book.at}

    def price(self, entry):
        """A price only when there is evidence for one, else 0 so the caller looks elsewhere."""
        book = self.books.get(entry)
        if not book:
            return 0
        if book._cached_at == book.at:
            return book._cached
        value = self._price(entry, book)
        book._cached, book._cached_at = value, book.at
        return value

    @staticmethod
    def _floor(book):
        """A 2 coin order filling beside 3.5m offers is a misclick, not a price."""
        return (sorted(book.asks)[len(book.asks) // 2] if book.asks else 0) / 100

    def sold_per_hour(self, entry):
        """Items sold into buy orders, per hour: what a new order could expect to catch."""
        book = self.books.get(entry)
        if not book:
            return 0
        floor = self._floor(book)
        return sum(n for _, p, n in book.sold if p >= floor) / (WINDOW / 3_600_000)

    def sold_price(self, entry):
        """What sellers have really taken. A new order only catches them at around this, whatever
        the best bid says."""
        book = self.books.get(entry)
        if not book:
            return 0
        floor = self._floor(book)
        rows = [(p, n) for _, p, n in book.sold if p >= floor]
        total = sum(n for _, n in rows)
        return sum(p * n for p, n in rows) / total if total else 0

    def daily(self, entry):
        """(sold, bought) a day: units sellers sold into buy orders and units buyers took from sell
        offers, the pair the Vol hover line shows for a bazaar item. Counted once per snapshot, since a tooltip asks far more often than the book moves."""
        book = self.books.get(entry)
        if not book:
            return 0, 0
        if book._daily_at == book.at:
            return book._daily
        floor = self._floor(book)
        days = WINDOW / 86_400_000
        sold = sum(n for _, p, n in book.sold if p >= floor)
        both = sum(n for _, p, n in book.fills if p >= floor)
        # Whole units a day: a week's total over 7, rounded down.
        book._daily, book._daily_at = (int(sold / days), int(max(both - sold, 0) / days)), book.at
        return book._daily

    def volume(self, entry):
        """Items traded a day, both sides."""
        book = self.books.get(entry)
        if not book:
            return 0
        floor = self._floor(book)
        return sum(n for _, p, n in book.fills if p >= floor) / (WINDOW / 86_400_000)

    def _price(self, entry, book):
        npc = self.npc(entry) or 0
        floor = self._floor(book)
        sold = collections.Counter()
        for _, price, amount in book.fills:
            if price >= floor:
                sold[price] += amount
        total = sum(sold.values())
        if total and len(book.samples) >= THIN_SAMPLES:
            half, seen = total / 2, 0
            for price in sorted(sold):
                seen += sold[price]
                if seen >= half:
                    if (total >= MIN_ITEMS and len(book.samples) >= MIN_SAMPLES) or not book.ask or price <= book.ask:
                        return max(price, npc)
                    break
        # Only where the book is real on both sides: the middle of a tight spread.
        # The wide books are the trap. Obsidian has sat at 1 coin against 500m offers, and an
        # offer nobody takes is not a price: one fed a craft chain up to 89 trillion.
        if book.bid and book.ask and book.ask <= book.bid * 2 and book.bid >= npc:
            return (book.bid + book.ask) / 2
        return 0

    def ask(self, entry):
        """The cheapest offer, when it could be real. For a last resort, never a first choice."""
        book = self.books.get(entry)
        if not book or not book.ask:
            return 0
        npc = self.npc(entry) or 0
        if npc and book.ask > npc * 1000:
            return 0
        return book.ask
