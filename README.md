# DSM (Dragsim Market)

Prices, deals, tracked flips and alerts for the Dragsim Minecraft server: the site at
[dsmnet.xyz](https://dsmnet.xyz/) and the Fabric mod that links your account to it.

| Folder | What it is |
| --- | --- |
| `server/` | The market service: prices every item from the auction house and bazaar, and serves the site. Python standard library only. |
| `server/site/` | The website. Plain HTML, CSS and JavaScript, no build step. |
| `mod/` | The DSM Fabric mod: account linking, price lines on hover, deal alerts, optional trade tracking. |

## The mod

Get it from [Releases](../../releases). Each release is built by GitHub from the commit it names
and publishes the jar's SHA-256, so you can check what you run:

- **Against the hash:** `sha256sum -c dsm-<version>.jar.sha256`, or on Windows
  `Get-FileHash dsm-<version>.jar` and compare with the release.
- **Against the code:** build the named commit yourself with JDK 25
  (`cd mod && ./gradlew build`). The build is reproducible on any OS, so
  `mod/build/libs/dsm-<version>.jar` comes out byte for byte the same as the release.

Requires Minecraft 26.1.2, Fabric Loader 0.19.2 or newer, Fabric API and Java 25. How linking and
trade uploads work is in [`mod/README.md`](mod/README.md).

## The server

See [`server/README.md`](server/README.md). Run the tests with
`cd server && python -m unittest discover -s tests`.

This repository holds the code only. The live market's price history, trades, accounts and keys
are not here and never will be.

## Licence

AGPL-3.0, see [`LICENSE`](LICENSE). Parts of the pricing engine are adapted from Coflnet's
open-source auction tools, which are AGPL-3.0 too; see [`NOTICE`](NOTICE).
