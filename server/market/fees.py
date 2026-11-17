"""What the auction house takes.

Dragsim charges when you list, not when you collect. Every "Collected" line in the logs matches
its sale to the coin (900.0M, 375.0M, 150.0M, an MVP+'s 380,000,000), so there is no claim fee.

The rate comes from two server lines:

    Price set to 7.4B coins!
    You don't have enough coins for the listing fee of 185.1M coins!       2.5%

    You don't have enough coins for the listing fee of 26,266,800 coins!

The second is exactly 1,050,000,000 x 2.5% + 16,800, a price other players were listing that
item at that day, and 16,800 is 50 coins an hour for the 14 days every listing runs. So the fee
has a time part, too small to matter but worth having because it makes the number exact.

Under 100m the rate has not been seen. Hypixel, whose numbers Dragsim keeps copying, uses 1%
under 10m and 2% under 100m, so those are assumed until a menu or a log says otherwise. Dragsim has
no claim fee.
"""

HOURLY = 50
DEFAULT_HOURS = 14 * 24
# (price at or above, rate, seen in Dragsim's own output)
BRACKETS = ((100_000_000, 0.025, True), (10_000_000, 0.02, False), (0, 0.01, False))


def rate(price):
    for floor, value, _ in BRACKETS:
        if price >= floor:
            return value
    return BRACKETS[-1][1]


def rate_is_confirmed(price):
    for floor, _, seen in BRACKETS:
        if price >= floor:
            return seen
    return False


def listing_fee(price, hours=DEFAULT_HOURS):
    """Coins taken when a BIN goes up at this price, for the whole stack."""
    return int(price * rate(price) + HOURLY * hours)


def net_of_fee(price, hours=DEFAULT_HOURS):
    return price - listing_fee(price, hours)


def price_to_net(net, hours=DEFAULT_HOURS):
    """The list price that leaves this many coins after the fee. Solved rather than inverted
    because the rate steps at each bracket, so a plain division lands on the wrong side."""
    low, high = int(net), int(net * 1.04 + HOURLY * hours + 1)
    while low < high:
        mid = (low + high) // 2
        if net_of_fee(mid, hours) >= net:
            high = mid
        else:
            low = mid + 1
    return low
