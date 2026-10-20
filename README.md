# Lantern

A private, Loci-style AI chat app for Debian. Models run on your own machine
through [Ollama](https://ollama.com); chats are stored locally in
`~/.local/share/lantern/history.db`.

Built with Python 3 + GTK4 + libadwaita. Works on Debian 12 (bookworm) and newer.

## Install

```sh
# 1. Local model runtime (not in Debian's repos)
curl -fsSL https://ollama.com/install.sh | sh

# 2. Lantern (apt pulls in the GTK dependencies automatically)
sudo apt install ./lantern_0.1.0_all.deb
```

Launch **Lantern** from your app menu (or run `lantern`). Open the menu
(top right), download a model such as `gemma3:4b` or `qwen3:8b`, and start chatting.

Uninstall: `sudo apt remove lantern`

## Features

- Streaming replies with a Stop button
- Model picker and in-app model downloads with progress
- Conversation history sidebar (new / switch / delete)
- Configurable Ollama server address (defaults to `http://127.0.0.1:11434`)
- Enter to send, Shift+Enter for a newline, Ctrl+N new chat, Ctrl+Q quit

## Build from source

```sh
./build-deb.sh        # outputs dist/lantern_<version>_all.deb
python3 -m lantern    # run without installing (from src/, needs the GTK deps)
```

Bump the version in `src/lantern/__init__.py`.

## Layout

```
src/lantern/app.py      GTK4/libadwaita UI
src/lantern/ollama.py   Ollama HTTP client (stdlib only)
src/lantern/store.py    SQLite history + JSON settings
packaging/              launcher, .desktop entry, icon
build-deb.sh            builds the .deb with dpkg-deb
```
