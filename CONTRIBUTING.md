# Contributing

Thanks for helping with AssetBridge. There are three ways in:

- **Suggest something:** open an issue with the *Feature request* form. Good ideas end up in
  [`ROADMAP.md`](ROADMAP.md).
- **Report a bug:** open an issue with the *Bug report* form. Versions and the end of `cache\server.log` help most.
- **Change the code:** a pull request. For anything bigger than a small fix, open or comment on an issue first, so
  two people don't build the same thing.

## Making a change

1. Set it up with `setup.ps1`, or follow [`AGENTS.md` Part A](AGENTS.md#part-a-set-it-up).
2. Run `py -3 tools\check.py` before you start, so you know what already passes.
3. Make one change per pull request. Test it on a scratch library and a test server on other ports
   ([AGENTS D.3](AGENTS.md#d3-run-a-test-server-on-other-ports)), not on your real libraries.
4. Run `py -3 tools\check.py` again. If you add a test, name it `tools\test_<name>.py`; the check picks it up.
5. Update the docs that your change touches: `README.md` (what it does), `AGENTS.md` (how it works, the API
   table), `STATE_MACHINES.md` (a new job or state), and `ROADMAP.md` (move the item to *Done*).
6. Open the pull request and fill in the template, including what you tried in Cinema 4D or Blender and what you
   couldn't.

## Style

- Python: standard library first; `requirements.txt` is short on purpose.
- Web: vanilla JavaScript and CSS, no build step and no framework. three.js and Spark are vendored in `web/vendor/`.
- Comments say why, in plain words.
- Everything stays on `127.0.0.1`. Keep the request guard (`Handler._refused`) when you touch networking.
- Internal names (`bridge2_*` modules, the `bridge2/1` protocol, `cache/bridge2.db`) stay as they are; renaming
  them breaks existing scenes and scripts.

## Working with a coding agent

The repository is written for agents as much as for people. Open it in Claude Code, Codex, Cursor or Copilot
and ask for a roadmap item or a fix. [`AGENTS.md`](AGENTS.md) gives the agent the map, the recipes and the
checks (`CLAUDE.md` points Claude Code at it). Ask the agent to finish with Part D and to tell you what it
couldn't check itself.

## Licence

By contributing you agree that your contribution is licensed under the MIT licence in [`LICENSE`](LICENSE).
