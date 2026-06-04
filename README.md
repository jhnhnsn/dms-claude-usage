# Claude Usage — DankMaterialShell plugin

A [DankMaterialShell](https://github.com/AvengeMedia/DankMaterialShell) (dms) **DankBar** widget that shows your Claude usage at a glance:

- **Plan rate-limit windows** — the same data as Claude Code's `/usage` (5-hour session window, 7-day window, per-model windows), with **% used**, **reset times**, and progress bars.
- **Burn-rate trend** — a spike arrow (`↗ ↑ ⇈`) when you're using tokens *faster than your usual pace* (e.g. a heavy agent run), plus a pace projection that warns if you're on track to exhaust the 5-hour window before it resets.
- **Token counts** — today's tokens, a 7-day bar chart, and a per-model breakdown, parsed locally from your Claude Code logs.

The bar pill shows your **session %** (or token count if the API is unavailable) and turns amber/red as the window fills. Click it for the full popout.

> **Note on the plan-windows feature:** it reads your Claude OAuth token from `~/.claude/.credentials.json` and calls `https://api.anthropic.com/api/oauth/usage` — the same **undocumented** endpoint Claude Code's `/usage` uses. It's read-only (a `GET`), the token never leaves your machine, and the feature degrades gracefully (falls back to token counts) if the endpoint changes or rate-limits. See [Privacy & the undocumented endpoint](#privacy--the-undocumented-endpoint).

## Screenshots

_Coming soon — open the popout from the bar pill to see plan windows, the burn-rate trend, and the per-model token breakdown._

## Requirements

- **DankMaterialShell** (this is a dms plugin — it renders into the DankBar). Works under any compositor dms supports; **not** tied to niri/Hyprland/Sway.
- **Python 3** on `PATH` (the widget shells out to two small stdlib-only scripts).
- **Claude Code** installed and used at least once (so `~/.claude/projects/` and credentials exist). The plan-windows feature additionally needs a logged-in Claude subscription.

No third-party Python packages — everything uses the standard library.

## Install

Clone into your dms plugins directory:

```sh
git clone https://github.com/jhnhnsn/dms-claude-usage \
  ~/.config/DankMaterialShell/plugins/dms-claude-usage
```

Then restart dms so it discovers the plugin and enable it:

```sh
systemctl --user restart dms.service     # or however you run dms
dms ipc call plugins enable claudeUsage
```

Finally add the widget to your bar — in **dms Settings → DankBar**, add **Claude Usage** to a section (left/center/right). Or edit `~/.config/DankMaterialShell/settings.json` and add `"claudeUsage"` to a `barConfigs[].rightWidgets` (etc.) list.

The bundled Python scripts are resolved from the plugin's own directory, so no separate install step is needed. Runtime caches are written to `~/.cache/claude-usage/`.

### Updating

```sh
git -C ~/.config/DankMaterialShell/plugins/dms-claude-usage pull
dms ipc call plugins reload claudeUsage
```

## How it works

| Layer | File | Role |
|---|---|---|
| Widget | `ClaudeUsageWidget.qml` | DankBar pill + popout (QML / Quickshell) |
| Manifest | `plugin.json` | dms plugin descriptor |
| Token counts | `scripts/usage_data.py` | parses `~/.claude/projects/**/*.jsonl` for tokens/day/model + burn rate |
| Plan windows | `scripts/fetch_windows.py` | calls the OAuth usage API; caches results |

### The burn-rate trend

`usage_data.py` computes a **baseline** = the median of your *active* 15-minute blocks over the last 7 days (idle time excluded, so the baseline reflects your real working pace). The **recent** rate is tokens/min over the last 15 minutes. The arrow reflects `recent / baseline`:

| Arrow | Meaning | Threshold |
|---|---|---|
| (none) | normal | < 1.5× |
| `↗` | elevated | ≥ 1.5× |
| `↑` | high | ≥ 2× |
| `⇈` | burst | ≥ 4× |

The pace projection extrapolates current burn to the 5-hour reset; if it would reach ≥80% / ≥100%, the pill warns.

### A note on token "cost"

`usage_data.py` can compute an approximate dollar value from token counts using list API prices, but **if you're on a flat-rate subscription (Pro / Max) this is not money you spend** — you're billed a flat monthly fee, not per token. The widget therefore shows token *counts*, not dollars; your real plan consumption is the rate-limit windows. (The cost math is left in the script for anyone on metered API billing who wants it.)

## Privacy & the undocumented endpoint

- The plan-windows feature reads `~/.claude/.credentials.json` (your own file, mode `0600`) to get the OAuth access token, and sends it as a `Bearer` token to `https://api.anthropic.com/api/oauth/usage` and `/api/oauth/profile`. Both are **`GET`** requests. The token is never written anywhere else or sent to any third party.
- These endpoints are **undocumented** — the same ones Claude Code's `/usage` and account UI use. They may change or rate-limit without notice. On any error (including HTTP 429) the widget serves its last cached windows (up to 30 min old, marked "cached") and the pill falls back to the token count. Nothing breaks.
- Caches under `~/.cache/claude-usage/` contain only derived values (percentages, reset times, plan tier) — **no token, email, or account ID**.
- If you don't want any network/credential access, the token-count and burn-rate features work entirely offline from your logs; you'll just see "Plan limits unavailable" in the popout.

## Troubleshooting

- **Plugin not discovered** — dms scans the plugins dir at startup; after a fresh clone, fully restart dms (a reload may not pick up a brand-new directory).
- **"Plan limits — unavailable: rate limited"** — the usage endpoint is briefly throttling; it recovers on its own. Polling is every 10 minutes by default.
- **"token expired — open Claude Code"** — Claude Code refreshes the OAuth token on use; open it once and the widget will pick up the new token.
- **No data at all** — confirm `python3` is on `PATH` and `~/.claude/projects/` exists.

## License

[MIT](LICENSE).

---

*Not affiliated with Anthropic. "Claude" is a trademark of Anthropic. This plugin reads local Claude Code data and an undocumented account endpoint at your own discretion.*
