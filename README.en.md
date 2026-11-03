# TuneTidy (理音)

[中文](README.md) | English

Split a NetEase Cloud Music playlist into several new playlists by genre. TuneTidy builds the result locally first; new playlists are created in your account only after you have reviewed it, adjusted it and confirmed it yourself.

> **Unofficial project.** Not affiliated with, endorsed by or authorized by NetEase Cloud Music or its operator. TuneTidy only reads and writes playlist information in your own account and never downloads audio. See the [disclaimer](#disclaimer).

<!-- Demo GIF: docs/demo.gif (recorded by the author) -->

## Quick start

```bash
pipx install tunetidy
tunetidy login                      # scan a QR code with the NetEase Cloud Music app; the session goes to the system keychain
tunetidy playlists                  # list your playlists
tunetidy analyze "我喜欢的音乐"       # read genres and build a plan (no account changes; first run about 1.5 s per song, cached afterwards)
tunetidy ui                         # review in the browser, adjust, then confirm and create the playlists
```

You can also just run `tunetidy ui` and do the rest on the web page: scan the QR code to log in, pick one or more playlists, watch the reading progress, and land on the result when it finishes.

You can confirm and create the playlists on the web page: click "确认并开始建" (confirm and create), and a 6-digit code appears in the terminal running `tunetidy ui`; type it into the page to start. Or do it all from the command line:

```bash
tunetidy approve <plan-id>          # confirm in the terminal yourself (type the code shown on screen)
tunetidy apply <plan-id>            # create private playlists and add songs; rerun to resume after an interruption
```

`我喜欢的音乐` ("My liked music") is the name NetEase gives to your liked-songs playlist; pass any playlist name or id.

To undo, use the 历史 (History) page in the browser or run `tunetidy undo <plan-id or run-id>`. Before undoing, TuneTidy reads the playlists back: it deletes only the playlists it created that you haven't changed; from playlists you have changed it removes only the songs it added.

## Split modes

By default songs are split by top-level genre. `analyze` takes other split modes (seconds once tags are cached), and the web page can switch them at any time:

```bash
tunetidy analyze "我喜欢的音乐" --only 流行               # split out pop only; everything else goes to 其他 (Other)
tunetidy analyze "我喜欢的音乐" --only 流行 --match any   # count every song that carries a pop tag
tunetidy analyze "我喜欢的音乐" --level 2                 # split by second-level genre
tunetidy analyze "我喜欢的音乐" --top 5                   # at most 5 playlists (not counting Other)
tunetidy analyze "我喜欢的音乐" --groups my.toml --no-other  # your own groups, no Other playlist
```

Genre names are NetEase's Chinese genre names. For custom groups, see [taxonomies/examples/](taxonomies/examples/); the web page can import and export the same TOML format.

## Web page

`tunetidy ui` opens TuneTidy in your browser (listening on localhost only). If you aren't logged in, it shows a QR code to scan with the NetEase Cloud Music app; the session goes to the system keychain as usual, and the sidebar shows the current account. Click 新整理 (new tidy-up) to pick playlists; 我喜欢的音乐 is selected by default. Several playlists are merged in the order you tick them, and a song in more than one counts once. While reading, the page shows progress, the estimated time left and the genres found so far. Closing the page doesn't stop it, the home page picks it up again, and you can cancel; genres already read stay cached.

The home page has one card per playlist. Open one to see which new playlists will be created and how many songs each gets. There you can switch split modes, rename playlists, move misplaced songs, and pick a home for each song TuneTidy couldn't sort. All of this changes only the local plan and never contacts NetEase.

After you confirm, the page shows the progress of creating the playlists; if it stops halfway you can continue, and no song is added twice. The History page lists every batch of playlists TuneTidy created. Before undoing, it checks what those playlists look like now; from any you have changed, it removes only the songs it added. After you tidy the same playlist again and create the new batch, the page offers to undo the previous batch made by the same account; undoing it needs its own confirmation and never happens on its own.

When a playlist has been tidied several times, the older plans are under 以前的整理 (earlier tidy-ups) at the top right of the result page. Plans that never created playlists aren't deleted automatically; click 现在清理 (clean up now) there. It deletes only records on this computer and keeps every plan that created playlists.

Everything on the web page that changes your NetEase account (creating, continuing, undoing) needs a code; logging in, picking playlists, reading and cleaning up local records don't change the account and don't need one. The code appears only in the terminal window that started TuneTidy and never passes through the web page. If TuneTidy was not started from a terminal window (for example, another program runs it in the background), web confirmation is unavailable and the page shows terminal commands instead.

The interface is in Chinese.

## Use it from Claude Code or Codex (MCP)

TuneTidy ships an MCP server, so an AI assistant can list playlists, build a plan, browse it, and create the playlists once you have confirmed:

```bash
claude mcp add tunetidy -- tunetidy mcp     # Claude Code
```

For Codex, add to `~/.codex/config.toml`:

```toml
[mcp_servers.tunetidy]
command = "tunetidy"
args = ["mcp"]
```

Five tools: `list_playlists`, `create_plan`, `get_job_status`, `get_plan_items`, `apply_plan`. Analysis and playlist creation run in the background: the tools return a job id at once, and `get_job_status` reports progress. **There is no confirmation tool.** Only you can confirm, by running `tunetidy approve` in a terminal, and `apply_plan` accepts only plans you have confirmed and that haven't changed since.

## Results

The author's own liked-songs playlist (1033 songs, 2026-10-31), default split (top-level genre):

| | Songs | Unsorted |
| --- | --- | --- |
| All | 1033 | 2.7% |
| Tagged with a genre by NetEase | 1026 (99.3%) | 2.1% |
| No genre tag | 7 | 85.7% |

Unsorted songs are left out of the new playlists until you place them in the web page. The share of manual changes will mean something only once more people have used TuneTidy, so it isn't listed yet. These numbers come from a single playlist and yours may differ; run `uv run python scripts/evaluate.py <plan-id>` to compute your own.

## Safety rules

- TuneTidy only creates playlists. It never changes or deletes playlists you already have. New playlists are private by default.
- Confirming and undoing always need you to type a code. On the command line, `approve`, `apply` (including `--yes`) and `undo` work only in an interactive terminal; on the web page, the code appears only in the terminal that started TuneTidy. If you use an AI assistant such as Claude Code or Codex, don't auto-allow these commands in its permission settings, and don't tell it the code.
- Too many wrong codes pause web confirmation for 10 minutes (restarting TuneTidy also clears it).
- Any change to a plan after you confirm it voids the confirmation. A plan that has been applied can't be applied a second time.
- Requests to NetEase are rate-limited with random pauses. The limits come from the config file only; no command or MCP tool can speed them up. Connections go direct, without the system proxy.
- Login credentials live in the system keychain and are never printed in logs or error messages. The web server listens on 127.0.0.1 only and checks a random token.

## FAQ

**Why is the first analysis so slow?** Genre tags have to be fetched one song at a time, and TuneTidy goes slowly to avoid being rate-limited: about 25 minutes for 1000 songs. Tags are cached, so switching split modes or analyzing again takes seconds.

**Can I close the page while it reads?** Yes. As long as the terminal window that started TuneTidy stays open, reading continues in the background and the home page shows its progress.

**What does "unsorted" (没分好) mean?** NetEase gave the song no genre, or several genres tie and TuneTidy can't tell which comes first. Unsorted songs are left out of the new playlists unless you place them on the web page's unsorted-songs view.

**Can an AI assistant confirm for me?** No. The code appears only in the terminal that started TuneTidy, never in the web page, the API or the logs, and the MCP server has no confirmation tool. An assistant can only apply plans you have already confirmed.

**How do I undo?** Click 撤销 (undo) on the History page, or run `tunetidy undo <plan-id or run-id>` in a terminal.

## Disclaimer

- This is a personal open-source project with no connection to NetEase Cloud Music or its operator.
- TuneTidy reaches your account through the non-public interfaces used by the NetEase Cloud Music website. They may change or stop working at any time, and using them may breach the platform's terms of service. Any restriction placed on your account as a result is your own risk.
- TuneTidy only reads and writes playlist information in your own account (names, descriptions, song lists). It does not download or distribute any audio.
- The software is provided "as is", without warranty of any kind; see [LICENSE](LICENSE).

## Development

This section is for people who want to work on TuneTidy's code; you don't need it to use the tool.

From a source checkout, run commands through `uv run` (for example `uv run tunetidy playlists`), or install the checkout as a command once with `uv tool install --editable .`.

```bash
uv sync
uv run ruff format . && uv run ruff check .
uv run mypy src
uv run pytest                               # offline tests: no network, a fake keychain
uv run pytest -m live                       # read-only tests against the real service; log in first
```

The front end lives in `web/` (React, TypeScript, Vite, Tailwind). Its build output isn't committed, so build it once before running `tunetidy ui` from source:

```bash
cd web && npm ci && npm run build            # writes to src/tunetidy/server/static/
```

While working on the front end, use `npm run dev`: in another terminal run `uv run tunetidy ui --no-browser`, then open `http://localhost:5173/#token=...` with the token it prints. For the code layout and key design decisions, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (in Chinese).
