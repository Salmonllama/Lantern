"""Lantern: a private, local-AI chat app for Debian (GTK4 + libadwaita).

Targets GTK 4.8 / libadwaita 1.2 (Debian 12 bookworm) and newer.
"""

import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import __version__  # noqa: E402
from .ollama import OllamaClient, OllamaError  # noqa: E402
from .store import Settings, Store, title_from  # noqa: E402

APP_ID = "io.github.lantern.Lantern"

CSS = """
.bubble { padding: 10px 14px; border-radius: 14px; }
.bubble.user { background: alpha(@accent_bg_color, 0.18); }
.bubble.assistant { background: alpha(@card_fg_color, 0.06); }
.bubble.error { background: alpha(@error_bg_color, 0.18); }
.role { font-size: 0.8em; font-weight: bold; opacity: 0.6; }
.composer { border-radius: 12px; padding: 8px; background: alpha(@card_fg_color, 0.05); }
.composer textview, .composer textview text { background: transparent; }
"""


class Bubble(Gtk.Box):
    def __init__(self, role, text=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        is_user = role == "user"
        self.set_halign(Gtk.Align.END if is_user else Gtk.Align.START)
        self.set_margin_start(60 if is_user else 0)
        self.set_margin_end(0 if is_user else 60)
        self.add_css_class("bubble")
        self.add_css_class(role)

        caption = Gtk.Label(label={"user": "You", "error": "Error"}.get(role, "Lantern"))
        caption.set_xalign(0)
        caption.add_css_class("role")
        self.append(caption)

        self.label = Gtk.Label(label=text or "…")
        self.label.set_wrap(True)
        self.label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label.set_xalign(0)
        self.label.set_selectable(True)
        self.append(self.label)
        self.text = text

    def append_text(self, chunk):
        self.text += chunk
        self.label.set_label(self.text)


class LanternWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Lantern")
        self.set_default_size(1000, 700)
        self.settings = Settings()
        self.store = Store()
        self.client = OllamaClient(self.settings["host"])
        self.conversation_id = None
        self.cancel = None
        self.busy = False

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(260)
        paned.set_shrink_start_child(False)
        paned.set_resize_start_child(False)
        self.toasts.set_child(paned)
        paned.set_start_child(self._build_sidebar())
        paned.set_end_child(self._build_chat())

        self.reload_sidebar()
        self.refresh_models()

    # ---- layout --------------------------------------------------------
    def _build_sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_title_widget(Gtk.Label(label="Chats"))
        new_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New chat (Ctrl+N)")
        new_btn.connect("clicked", lambda *_: self.new_chat())
        header.pack_start(new_btn)
        box.append(header)

        self.chat_list = Gtk.ListBox()
        self.chat_list.add_css_class("navigation-sidebar")
        self.chat_list.connect("row-activated", self._on_row_activated)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self.chat_list)
        box.append(scroll)
        return box

    def _build_chat(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)

        self.model_names = Gtk.StringList()
        self.model_drop = Gtk.DropDown(model=self.model_names)
        self.model_drop.set_tooltip_text("Model")
        self.model_drop.connect("notify::selected", self._on_model_changed)
        header.set_title_widget(self.model_drop)

        delete_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete this chat")
        delete_btn.connect("clicked", lambda *_: self.delete_chat())
        header.pack_start(delete_btn)

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Settings & models")
        menu_btn.set_popover(self._build_settings_popover())
        header.pack_end(menu_btn)
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh models")
        refresh_btn.connect("clicked", lambda *_: self.refresh_models())
        header.pack_end(refresh_btn)
        box.append(header)

        # Messages area + empty state
        self.stack = Gtk.Stack(vexpand=True)
        self.empty = Adw.StatusPage(
            icon_name="io.github.lantern.Lantern",
            title="Ask anything",
            description="Everything runs on this computer through Ollama.\n"
            "Nothing leaves your machine.",
        )
        self.stack.add_named(self.empty, "empty")

        self.messages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for side in ("top", "bottom", "start", "end"):
            getattr(self.messages_box, f"set_margin_{side}")(18)
        clamp = Adw.Clamp(maximum_size=820)
        clamp.set_child(self.messages_box)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_child(clamp)
        self.stack.add_named(self.scroll, "chat")
        box.append(self.stack)

        # Composer
        composer = Gtk.Box(spacing=8)
        composer.add_css_class("composer")
        self.input = Gtk.TextView(hexpand=True)
        self.input.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.input.set_accepts_tab(False)
        self.input.set_top_margin(6)
        self.input.set_bottom_margin(6)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.input.add_controller(keys)
        input_scroll = Gtk.ScrolledWindow(hexpand=True)
        input_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        input_scroll.set_max_content_height(160)
        input_scroll.set_propagate_natural_height(True)
        input_scroll.set_child(self.input)
        composer.append(input_scroll)

        self.send_btn = Gtk.Button(icon_name="mail-send-symbolic", tooltip_text="Send (Enter)")
        self.send_btn.set_valign(Gtk.Align.END)
        self.send_btn.add_css_class("suggested-action")
        self.send_btn.add_css_class("circular")
        self.send_btn.connect("clicked", lambda *_: self.on_send_or_stop())
        composer.append(self.send_btn)

        clamp2 = Adw.Clamp(maximum_size=820)
        clamp2.set_child(composer)
        for side in ("bottom", "start", "end"):
            getattr(clamp2, f"set_margin_{side}")(14)
        box.append(clamp2)
        return box

    def _build_settings_popover(self):
        pop = Gtk.Popover()
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("top", "bottom", "start", "end"):
            getattr(grid, f"set_margin_{side}")(12)
        grid.set_size_request(320, -1)

        grid.append(self._heading("Ollama server"))
        self.host_entry = Gtk.Entry(text=self.settings["host"])
        self.host_entry.connect("activate", self._on_host_set)
        grid.append(self.host_entry)
        save = Gtk.Button(label="Connect")
        save.connect("clicked", self._on_host_set)
        grid.append(save)

        grid.append(Gtk.Separator(margin_top=6, margin_bottom=6))
        grid.append(self._heading("Download a model"))
        hint = Gtk.Label(label="e.g. gemma3:4b, qwen3:8b, llama3.2:3b", xalign=0)
        hint.add_css_class("dim-label")
        grid.append(hint)
        self.pull_entry = Gtk.Entry(placeholder_text="model name")
        self.pull_entry.connect("activate", self._on_pull)
        grid.append(self.pull_entry)
        self.pull_btn = Gtk.Button(label="Download")
        self.pull_btn.connect("clicked", self._on_pull)
        grid.append(self.pull_btn)
        self.pull_bar = Gtk.ProgressBar(show_text=True, visible=False)
        grid.append(self.pull_bar)

        grid.append(Gtk.Separator(margin_top=6, margin_bottom=6))
        about = Gtk.Button(label="About Lantern")
        about.connect("clicked", lambda *_: (pop.popdown(), self.show_about()))
        grid.append(about)
        pop.set_child(grid)
        return pop

    @staticmethod
    def _heading(text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.add_css_class("heading")
        return lbl

    # ---- helpers -------------------------------------------------------
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def scroll_to_bottom(self):
        def go():
            adj = self.scroll.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return False

        GLib.timeout_add(30, go)

    def current_model(self):
        item = self.model_drop.get_selected_item()
        return item.get_string() if item else ""

    def set_busy(self, busy):
        self.busy = busy
        if busy:
            self.send_btn.set_icon_name("media-playback-stop-symbolic")
            self.send_btn.set_tooltip_text("Stop")
            self.send_btn.remove_css_class("suggested-action")
            self.send_btn.add_css_class("destructive-action")
        else:
            self.send_btn.set_icon_name("mail-send-symbolic")
            self.send_btn.set_tooltip_text("Send (Enter)")
            self.send_btn.remove_css_class("destructive-action")
            self.send_btn.add_css_class("suggested-action")

    def clear_messages(self):
        child = self.messages_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.messages_box.remove(child)
            child = nxt

    def add_bubble(self, role, text=""):
        bubble = Bubble(role, text)
        self.messages_box.append(bubble)
        self.stack.set_visible_child_name("chat")
        self.scroll_to_bottom()
        return bubble

    # ---- models --------------------------------------------------------
    def refresh_models(self):
        def work():
            try:
                models = self.client.list_models()
                GLib.idle_add(self._set_models, models, None)
            except OllamaError as e:
                GLib.idle_add(self._set_models, [], str(e))

        threading.Thread(target=work, daemon=True).start()

    def _set_models(self, models, error):
        self._updating_models = True
        self.model_names.splice(0, self.model_names.get_n_items(), models)
        self._updating_models = False
        if error:
            self.empty.set_title("Can't reach Ollama")
            self.empty.set_description(
                error + "\n\nInstall Ollama from ollama.com, then press refresh."
            )
        elif not models:
            self.empty.set_title("No models yet")
            self.empty.set_description(
                "Open the menu (top right) and download one, e.g. gemma3:4b."
            )
        else:
            self.empty.set_title("Ask anything")
            self.empty.set_description(
                "Everything runs on this computer through Ollama.\nNothing leaves your machine."
            )
            wanted = self.settings["model"]
            idx = models.index(wanted) if wanted in models else 0
            self.model_drop.set_selected(idx)
        return False

    def _on_model_changed(self, *_):
        if getattr(self, "_updating_models", False):
            return
        model = self.current_model()
        if model:
            self.settings["model"] = model

    def _on_host_set(self, *_):
        host = self.host_entry.get_text().strip() or Settings.DEFAULTS["host"]
        self.settings["host"] = host
        self.client = OllamaClient(host)
        self.toast(f"Using {host}")
        self.refresh_models()

    def _on_pull(self, *_):
        name = self.pull_entry.get_text().strip()
        if not name or self.pull_btn.get_sensitive() is False:
            return
        self.pull_btn.set_sensitive(False)
        self.pull_bar.set_visible(True)
        self.pull_bar.set_fraction(0)
        self.pull_bar.set_text("Starting…")

        def update(status, frac):
            self.pull_bar.set_text(status if frac is None else f"{status} {frac:.0%}")
            if frac is None:
                self.pull_bar.pulse()
            else:
                self.pull_bar.set_fraction(frac)
            return False

        def done(error):
            self.pull_btn.set_sensitive(True)
            self.pull_bar.set_visible(False)
            if error:
                self.toast(error)
            else:
                self.toast(f"Downloaded {name}")
                self.settings["model"] = name
                self.pull_entry.set_text("")
                self.refresh_models()
            return False

        def work():
            try:
                for status, frac in self.client.pull(name):
                    GLib.idle_add(update, status, frac)
                GLib.idle_add(done, None)
            except OllamaError as e:
                GLib.idle_add(done, str(e))

        threading.Thread(target=work, daemon=True).start()

    # ---- conversations -------------------------------------------------
    def reload_sidebar(self):
        self._clear_list()  # ListBox.remove_all() needs GTK 4.12; bookworm has 4.8
        for row in self.store.conversations():
            r = Gtk.ListBoxRow()
            r.conversation_id = row["id"]
            lbl = Gtk.Label(label=row["title"], xalign=0, ellipsize=Pango.EllipsizeMode.END)
            lbl.set_margin_top(8)
            lbl.set_margin_bottom(8)
            lbl.set_margin_start(6)
            r.set_child(lbl)
            self.chat_list.append(r)
            if row["id"] == self.conversation_id:
                self.chat_list.select_row(r)

    def _clear_list(self):
        while (row := self.chat_list.get_row_at_index(0)) is not None:
            self.chat_list.remove(row)

    def _on_row_activated(self, _list, row):
        if self.busy:
            self.toast("Wait for the reply to finish (or stop it) first")
            return
        self.open_chat(row.conversation_id)

    def open_chat(self, conversation_id):
        self.conversation_id = conversation_id
        self.clear_messages()
        msgs = self.store.messages(conversation_id)
        for m in msgs:
            self.add_bubble(m["role"], m["content"])
        self.stack.set_visible_child_name("chat" if msgs else "empty")
        self.input.grab_focus()

    def new_chat(self):
        if self.busy:
            return
        self.conversation_id = None
        self.clear_messages()
        self.chat_list.unselect_all()
        self.stack.set_visible_child_name("empty")
        self.input.grab_focus()

    def delete_chat(self):
        if self.conversation_id is None or self.busy:
            return
        dialog = Adw.MessageDialog(
            transient_for=self, heading="Delete this chat?",
            body="It will be removed from this computer permanently.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, resp):
            if resp == "delete":
                self.store.delete(self.conversation_id)
                self.new_chat()
                self.reload_sidebar()

        dialog.connect("response", on_response)
        dialog.present()

    # ---- sending -------------------------------------------------------
    def _on_key(self, _ctrl, keyval, _code, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not (
            state & Gdk.ModifierType.SHIFT_MASK
        ):
            if not self.busy:
                self.on_send_or_stop()
            return True
        return False

    def on_send_or_stop(self):
        if self.busy:
            if self.cancel:
                self.cancel.set()
            return
        buf = self.input.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if not text:
            return
        model = self.current_model()
        if not model:
            self.toast("No model selected. Download one from the menu first.")
            return
        buf.set_text("")

        if self.conversation_id is None:
            self.conversation_id = self.store.new_conversation(title_from(text), model)
            self.reload_sidebar()
        self.store.add_message(self.conversation_id, "user", text)
        self.add_bubble("user", text)
        history = self.store.messages(self.conversation_id)
        bubble = self.add_bubble("assistant")
        conv_id = self.conversation_id

        self.cancel = threading.Event()
        cancel = self.cancel
        self.set_busy(True)

        def chunk(c):
            bubble.append_text(c)
            self.scroll_to_bottom()
            return False

        def finish(error):
            reply = bubble.text
            if reply:
                if cancel.is_set():
                    reply += "\n\n[stopped]"
                    bubble.label.set_label(reply)
                self.store.add_message(conv_id, "assistant", reply)
            elif not error:
                bubble.label.set_label("[stopped]" if cancel.is_set() else "[no reply]")
            if error:
                if not reply:
                    self.messages_box.remove(bubble)
                self.add_bubble("error", error)
            self.set_busy(False)
            self.reload_sidebar()
            return False

        def work():
            try:
                for c in self.client.chat(model, history, cancel):
                    GLib.idle_add(chunk, c)
                GLib.idle_add(finish, None)
            except (OllamaError, ValueError) as e:
                GLib.idle_add(finish, str(e))

        threading.Thread(target=work, daemon=True).start()

    def show_about(self):
        about = Adw.AboutWindow(
            transient_for=self,
            application_name="Lantern",
            application_icon=APP_ID,
            version=__version__,
            comments="Private AI chat that runs on your own computer.",
            license_type=Gtk.License.MIT_X11,
        )
        about.present()


class LanternApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
                         if hasattr(Gio.ApplicationFlags, "DEFAULT_FLAGS")
                         else Gio.ApplicationFlags.FLAGS_NONE)
        self.connect("activate", self.on_activate)

    def on_activate(self, _app):
        win = self.get_active_window()
        if win is None:
            provider = Gtk.CssProvider()
            if hasattr(provider, "load_from_string"):
                provider.load_from_string(CSS)
            else:
                provider.load_from_data(CSS.encode())
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            win = LanternWindow(self)
            new = Gio.SimpleAction.new("new-chat", None)
            new.connect("activate", lambda *_: win.new_chat())
            self.add_action(new)
            self.set_accels_for_action("app.new-chat", ["<Control>n"])
            quit_ = Gio.SimpleAction.new("quit", None)
            quit_.connect("activate", lambda *_: self.quit())
            self.add_action(quit_)
            self.set_accels_for_action("app.quit", ["<Control>q"])
        win.present()


def main():
    return LanternApp().run(sys.argv)
