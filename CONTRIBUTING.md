# Contributing

**English** · [Français](CONTRIBUTING.fr.md)

Thanks for helping! The most valuable contribution right now needs no code: **tell us if it works on your iPhone**, with your iPhone model, iOS version and Windows language.

## Run it without an iPhone

```
python rocket.py --demo
```

Demo mode shows an invented library. It is the way to work on the interface, and the only source allowed for screenshots.

## Tests

No iPhone and no dependency needed:

```
python -m unittest discover -s tests
```

## Project map

| File | Role |
|---|---|
| `iphone.py` | USB protocols (usbmuxd, lockdownd, AFC). **Read-only.** |
| `rocket.py` | Local server, library, copy, deletion, demo mode |
| `index.html` | The whole interface, no build step |
| `locales/*.json` | Interface text |
| `wpd-map.cs` | Lists files as Windows sees them, and deletes through Windows' device API |

## Rules that protect users

1. **`iphone.py` stays read-only.** A test enforces it. Never add an AFC write or delete: it would bypass the Photos library.
2. **Deletion fails closed.** When identity cannot be proven, refuse. Any change there needs a test showing the guard refuses.
3. **No personal data in the repository.** No real photos, file lists or logs, not even in tests or screenshots.
4. **No dependencies.** Python's standard library only, so that installing stays trivial.
5. **Both languages, always.** Every interface text goes in `locales/en.json` and `locales/fr.json`. A test checks they match.

## Adding a language

1. Copy `locales/en.json` to `locales/xx.json` and translate it.
2. Add `xx` to `LANGS` and to the `#lang` menu in `index.html`.
3. Add the backup folder name to `DELETED_FOLDERS` and the confirmation word to `DELETE_WORDS` in `rocket.py`.
