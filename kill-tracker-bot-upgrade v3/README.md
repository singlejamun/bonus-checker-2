# Kill Tracker Bot

A Discord bot that reads kill claims ("@member 5x" + screenshot), sends them to a private review
channel with **Accept / Reject / Edit** buttons, logs approved kills with money (kills × price per kill),
and posts a weekly report **plus an Excel sheet** every **Sunday 8 AM**.

## What's new in this build

- **Review role ping** — every new claim tags the configured reviewer role(s)
- **Claim confirmation** — on Accept, the bot replies under the original message with kills + money
- **Reject reason templates** — Blurry / Wrong count / Duplicate / Not clear / Wrong player / Custom
- **Auto-reset confirmation** — after the Sunday Excel, a summary shows members, kills, money, pending
- **Missed Sunday recovery** — if the bot was offline at 8 AM, it posts the report as soon as it starts and notes that it was late
- **Leaderboard** — `??top` / `??lb` (this week or `last`)
- **Mid-week snapshot** — auto every Wednesday ~12:00 local + `??midweek` command; sends Excel only
- **Pending dashboard** — `??pending`
- **Member self-check** — `??me` / `??mykills`
- **Audit log** — Accept/Reject/Edit logged; `??auditchannel` + `??audit`
- **Backup** — `??backup` DMs you the database file
- **Rate limit** — max 5 claims per user per hour
- **`??edit <claim #>`** — fix or delete any claim after it was reviewed (kills, add/remove member, price per kill, delete)

## 1. Create the bot on Discord
1. Go to https://discord.com/developers/applications → **New Application** → **Bot** tab.
2. Click **Reset Token** and copy it (keep it secret).
3. On the same Bot tab turn **ON** `MESSAGE CONTENT INTENT` (required to read "5x" messages).
4. **OAuth2 → URL Generator**: scope `bot`; permissions: View Channels, Send Messages, Embed Links,
   Attach Files, Read Message History, Add Reactions, **Manage Channels** (for `??review`).
5. Open the generated link and add the bot to your server.

## 2. Run it
```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env   then put your token inside
python main.py
```
Keep it running 24/7 (a VPS, or any always-on PC). The weekly report only posts while the bot is online;
if it was offline at Sunday 8 AM it posts as soon as it starts again (with a "Missed Sunday recovery" note).

## 3. Set up in your server
| Command | What it does |
|---|---|
| `??review <name>` | Creates a **private** channel called `<name>`. Every claim is sent there for approval. |
| `??target <name>` | Run **inside a channel** to make it a target channel (the bot watches it). |
| `??target <name> 50000` | Same, fixed price **50,000 per kill**. |
| `??target <name> 50000,1000000,2000000` | Same, with a price list (the first price is the default). |
| `??untarget` | Stop watching this channel (old approved kills are kept). |
| `??targets` | List all target channels. |
| `??reportchannel [#channel]` | Where the Sunday report goes (default: the review channel). |
| `??reviewrole add/remove @role` | Let a role approve claims (they are also **tagged** on every new claim). |
| `??auditchannel [#channel]` | Live log of Accept / Reject / Edit. Omit channel to clear. |
| `??bonus <target name>` | Kills + money per member for that target, current week. |
| `??bonus @member` | One member's kills + money across all targets, current week. |
| `??bonus <name or @member> last` | Same for the previous full week. |
| `??bonus` | Everything, current week. |
| `??weekly` | Post last week's full report **and Excel sheet** + reset summary now. |
| `??sheet [last]` | Excel sheet only. |
| `??midweek` | Current-week Excel sheet only (no embeds). |
| `??pending` | List claims waiting for review. |
| `??top` `[last]` | Top 15 killers, one line each. |
| `??lb` `[last]` | Leaderboard podium: 2nd · 1st · 3rd side by side, then ranks 4-15. |
| `??me` / `??mykills` `[last]` | Your rank card: kills, money, rank, who is ahead / behind you, progress to #1 (anyone can use). |
| `??testbonus [last]` | **Test run**: sends the preview Excel sheet only. Nothing is paid. |
| `??audit` | Last 20 Accept/Reject/Edit actions. |
| `??backup` | DM you the database file. |
| `??help` | Show help. |

Staff = people with **Manage Server** permission, or a role added with `??reviewrole`.

## 4. How a claim works
1. In a target channel someone posts a message such as:
   ```
   @Rahul 5x
   @Aman 9k
   ```
   with the screenshot attached. `5x` and `5k` both mean **5 kills**.
2. The bot sends a card to the review channel, **tags the reviewer role(s)**, shows screenshot + kills + money.
   The original message gets ⏳.
3. **Accept** → kills are logged, original message gets ✅.
   **Reject** → pick a template reason (or Custom); original message gets ❌ + reason.
   **Edit** → change a member's kills (0 removes them), add a member, or change the price.
4. Buttons keep working after the bot restarts.
5. Max **5 claims per user per hour** (rate limit).

### Fixing a claim after review: `??edit <claim #>`
The claim number is on the card (`Kill claim #12`). Staff run `??edit 12` and get a panel with:
* **member dropdown** → change that member's kills (`0` removes them)
* **Add member** → add someone with kills
* **Edit price** → new price per kill (re-prices every member of that claim)
* **Delete claim** → asks for confirmation, then the claim is **not counted** anywhere (reports, `??top`, Excel).
  The original message gets 🗑️ and a short notice; the claim card turns grey.

Changes to an accepted claim update the bonus immediately. Removing the last member deletes the claim.
Rejected claims are already not counted, so they cannot be edited. A deleted message cannot be submitted again.
Every edit/delete is written to the audit log.

### Price per kill
* A price in the same message wins: `@Rahul 5x 100k` → 5 × 100,000. Also `1.5m`, `200000`.
* Otherwise the channel's default price (`??target name 50000`; for a list the first price).
* Otherwise `DEFAULT_PRICE` from `.env` (50,000).

### Weeks
A week runs **Sunday 08:00 → next Sunday 08:00** in `TIMEZONE` (default `Asia/Kolkata`, change in `.env`).
A kill belongs to the week in which the **original message was posted**, even if it is approved later.
Claims still pending are not counted; the report footer tells you how many are pending.

After the Sunday report the bot posts an **auto-reset confirmation** (members, total kills, total money, pending).
Counters for the new week start at 0 automatically (data is kept for `last` queries).

### Excel sheet and warnings
Columns: Name, kills + total per target, **Subtotal, Warning, Cut %, Cut Amount, Grand Total**.
The Warning column is a number (1, 2 or 3). Cut % is read from the Warnings table on the right of the sheet
(default 10% / 30% / 50%, editable in the sheet). Grand Total = Subtotal - Cut Amount.

## 5. Tests
```bash
python -m unittest discover -s tests -t . -v
```

## 6. Backup / restore
Everything is in one file: `data/killbot.db`. Use `??backup` or stop the bot and copy that file.
Copy it back to restore.

## 7. Known limits
* Accept/Reject are final, but an accepted claim can be fixed or deleted with `??edit <claim #>`. Delete itself has no undo.
* If someone edits their original message after posting, the bot does not re-read it.
* The Edit member picker shows the first 25 members of a claim.
* Needs discord.py 2.4 or newer.
* Rate limit is 5 claims / user / hour (change `MAX_CLAIMS_PER_HOUR` in `bot/cogs/claims.py` if needed).
