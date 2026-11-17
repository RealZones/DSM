# DSM (Dragsim Market) for Fabric

Requires Minecraft 26.1.2, Fabric Loader 0.19.2 or newer, Fabric API for 26.1.2, and Java 25. Put `dsm-<version>.jar` from [Releases](../../../releases) in your instance's `mods` folder.

1. Join Dragsim and run `/dsm link`.
2. Copy the one-time code shown in Minecraft and open the market site from the same screen.
3. Enter the code on the site. Add price targets in Notifier to get your own alerts.

Run `/dsm` for link status and `/dsm site` for the browser link. `/dsm alerts off` pauses chat alerts for the running game. Click a deal or listing alert to open the seller's auction house.

Item stacks and saved recipe icons use their server ID. Text-only chat item hovers show prices from a matching owned item when its name and lore agree, otherwise an estimate for the upgrades visible in the message. Unknown catalog names are not guessed. Recipe hovers refresh while open. Bazaar Orders hovers show the highest buy order, lowest sell offer, and whether your order is behind the snapshot's best price. Snapshots refresh every 15 seconds while the orders screen is open; their age is shown, stale comparisons are suppressed, and equal prices do not establish queue position.

When Endsight is installed, DSM negotiates compressed downloads for its profile browser and decodes the responses before the existing validation. This reduces profile and catalog transfers without changing their source or pricing rules.

Trade uploads are off by default. `/market trades on` opts in to sending completed trade receipts, item details from the completed trade window and matching auction, upgrade, forge and Bazaar messages to your own market history. `/market trades off` pauses recording and sending. While linked, item and trade value lookups send the Dragsim items being priced so the service can account for upgrades. The mod records activity only on the main Dragsim server.

The link screen proves the signed-in Minecraft account to Mojang. The market receives the proof result and issues a separate credential, stored in this instance's `config/dragsim-market/account.properties`. Keep that file private. A new `/market link` renews the credential and issues a new browser code.

Build from this directory using `./gradlew.bat build` on Windows or `./gradlew build` on other platforms. The release jar is `build/libs/dsm-<version>.jar`, byte for byte the same as the release built from the same commit.
