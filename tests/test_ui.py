"""Browser tests for the chat UI. External services are faked (conftest); /upload, /api and
/api/stream are mocked with page.route wherever a test needs an exact server answer."""
import json
import re
import threading
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk
from playwright.sync_api import expect

NOT_FOUND = "I don't know from uploaded PDF."
TXT = {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello world"}


def sse(*events):
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


def stream_ok(text="Hello there", sources=()):
    return sse(("token", {"text": text}), ("sources", {"sources": list(sources)}), ("done", {}))


def mock_stream(page, body=None, status=200, seen=None):
    def handle(route):
        if seen is not None:
            seen.append(route.request)
        route.fulfill(status=status, body=body, headers={"content-type": "text/event-stream"})

    page.route("**/api/stream", handle)


def mock_upload(page, status="ingested", source="notes.txt", chunks=3, seen=None):
    def handle(route):
        if seen is not None:
            seen.append(route.request)
        route.fulfill(json={"status": status, "source": source, "chunks": chunks, "sha256": "x"})

    page.route("**/upload", handle)


@pytest.fixture
def app(page, live_url):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    # keep tests offline: the Google Fonts stylesheet is stubbed
    page.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(body="", content_type="text/css"))
    page.errors = errors
    page.goto(live_url)
    page.wait_for_load_state("networkidle")  # the first /session/files call has finished
    return page


def ask(page, text="What is this?"):
    page.fill("#chat-input", text)
    page.click("#send-btn")


# --- page shell -------------------------------------------------------------

def test_page_shows_title_sidebar_and_sheet(app):
    expect(app.get_by_role("heading", name="Ask your documents")).to_be_visible()
    expect(app.get_by_text("Files in this session")).to_be_visible()
    expect(app.locator("#file-count")).to_have_text("0 files")
    expect(app.locator("#sheet-sub")).to_have_text("Add a file first, then ask about it in plain words.")


def test_page_loads_without_console_errors(app):
    app.wait_for_load_state("networkidle")
    assert app.errors == []


def test_static_assets_are_served(app, live_url):
    for path in ("/static/style.css", "/static/app.js"):
        assert app.request.get(live_url + path).ok


def test_first_visit_shows_empty_state_with_three_example_questions(app):
    expect(app.get_by_role("heading", name="Start with a document, then ask a question.")).to_be_visible()
    expect(app.locator(".tip")).to_have_count(3)


def test_example_question_fills_the_input_without_sending(app):
    app.locator(".tip").first.click()
    expect(app.locator("#chat-input")).to_have_value("What are the main points of chapter 3?")
    expect(app.locator(".bubble.user")).to_have_count(0)


# --- upload + file list -----------------------------------------------------

def test_choosing_a_file_enables_upload_and_clear_resets_it(app):
    expect(app.locator("#upload-btn")).to_be_disabled()
    app.set_input_files("#file-input", TXT)
    expect(app.locator("#upload-btn")).to_be_enabled()
    expect(app.locator("#file-label-text")).to_have_text("notes.txt")
    app.click("#file-clear")
    expect(app.locator("#upload-btn")).to_be_disabled()
    expect(app.locator("#file-label-text")).to_have_text("Choose file…")


@pytest.mark.parametrize(
    "status, badge",
    [("ingested", "Added"), ("overwritten", "Updated"), ("skipped", "Already added")],
)
def test_upload_adds_a_file_row_with_passages_and_status_badge(app, status, badge):
    mock_upload(app, status=status, chunks=4)
    app.set_input_files("#file-input", TXT)
    app.click("#upload-btn")
    row = app.locator(".file-row")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("notes.txt")
    expect(row).to_contain_text("4 passages")
    expect(row.locator(".badge")).to_have_text(badge)
    expect(app.locator("#file-count")).to_have_text("1 file")
    expect(app.locator("#sheet-sub")).to_contain_text("4 passages in all")


def test_reuploading_same_name_updates_the_row_instead_of_duplicating(app):
    mock_upload(app, chunks=2)
    for _ in range(2):
        app.set_input_files("#file-input", TXT)
        app.click("#upload-btn")
        expect(app.locator("#upload-btn")).to_be_enabled()
    expect(app.locator(".file-row")).to_have_count(1)


def test_upload_error_is_shown_in_the_status_line(app):
    app.route("**/upload", lambda r: r.fulfill(status=400, json={"detail": "Uploaded file is empty"}))
    app.set_input_files("#file-input", TXT)
    app.click("#upload-btn")
    expect(app.locator("#upload-status")).to_have_text("Uploaded file is empty")
    expect(app.locator(".file-row")).to_have_count(0)


def test_drag_over_highlights_the_drop_card(app):
    app.dispatch_event("#dropzone", "dragover")
    expect(app.locator("#dropzone")).to_have_class(re.compile("dragover"))
    app.dispatch_event("#dropzone", "dragleave")
    expect(app.locator("#dropzone")).not_to_have_class(re.compile("dragover"))


def test_dropping_a_file_uploads_it(app):
    mock_upload(app)
    app.evaluate(
        """() => {
          const dt = new DataTransfer();
          dt.items.add(new File(['hello'], 'dropped.txt', {type: 'text/plain'}));
          document.getElementById('dropzone').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}));
        }"""
    )
    expect(app.locator(".file-row")).to_have_count(1)


def test_attach_button_picks_and_uploads_in_one_step(app):
    mock_upload(app)
    with app.expect_file_chooser() as chooser:
        app.click("#attach-btn")
    chooser.value.set_files(TXT)
    expect(app.locator(".file-row")).to_have_count(1)


def test_big_file_tries_presign_then_falls_back_to_plain_upload(app):
    seen = []
    app.route("**/presign", lambda r: (seen.append("presign"), r.fulfill(status=404, json={"detail": "no s3"}))[1])
    mock_upload(app, source="big.txt")
    app.on("request", lambda r: seen.append("upload") if r.url.endswith("/upload") else None)
    app.set_input_files("#file-input", {"name": "big.txt", "mimeType": "text/plain", "buffer": b"x" * (5 * 1024 * 1024)})
    app.click("#upload-btn")
    expect(app.locator(".file-row")).to_have_count(1)
    assert seen == ["presign", "upload"]


# --- chat: flow guards ------------------------------------------------------

def test_requests_use_the_server_cookie_not_a_client_chosen_session_id(app):
    seen = []
    mock_upload(app, seen=seen)
    mock_stream(app, stream_ok(), seen=seen)
    app.set_input_files("#file-input", TXT)
    app.click("#upload-btn")
    ask(app)
    expect(app.locator(".bubble.bot")).to_have_text("Hello there")
    assert len(seen) == 2
    assert all("x-session-id" not in r.headers for r in seen)
    assert all("kb_sid=" in r.headers.get("cookie", "") for r in seen)


def test_send_with_button_renders_user_bubble_and_bot_card(app):
    mock_stream(app, stream_ok("Hello there"))
    ask(app, "Hi")
    expect(app.locator(".bubble.user")).to_have_text("Hi")
    expect(app.locator(".bubble.bot")).to_have_text("Hello there")
    expect(app.locator(".empty-state")).to_have_count(0)
    expect(app.locator(".bubble.typing")).to_have_count(0)


def test_enter_sends_and_shift_enter_adds_a_newline(app):
    mock_stream(app, stream_ok())
    app.fill("#chat-input", "line one")
    app.press("#chat-input", "Shift+Enter")
    app.keyboard.type("line two")
    assert app.input_value("#chat-input") == "line one\nline two"
    app.press("#chat-input", "Enter")
    expect(app.locator(".bubble.user")).to_have_text("line one\nline two")


def test_blank_question_is_not_sent(app):
    seen = []
    mock_stream(app, stream_ok(), seen=seen)
    app.fill("#chat-input", "   ")
    app.press("#chat-input", "Enter")
    app.wait_for_timeout(200)
    assert seen == [] and app.locator(".bubble.user").count() == 0


def test_history_is_sent_and_holds_the_full_streamed_answer(app):
    seen = []
    mock_stream(app, stream_ok("First answer"), seen=seen)
    ask(app, "one")
    expect(app.locator(".bubble.bot")).to_have_text("First answer")
    expect(app.locator("#send-btn")).to_be_enabled()
    ask(app, "two")
    expect(app.locator(".bubble.user")).to_have_count(2)
    history = json.loads(seen[1].post_data)["history"]
    assert history == [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "two"},
    ]


def test_sources_show_as_from_chips_without_duplicates(app):
    mock_stream(app, stream_ok("Answer", sources=["a.pdf", "b.md"]))
    ask(app)
    chips = app.locator(".source-chip")
    expect(chips).to_have_count(2)
    expect(chips.first).to_contain_text("a.pdf")
    expect(app.locator(".sources")).to_contain_text("From")


def test_no_sources_means_no_chips(app):
    mock_stream(app, stream_ok("Answer"))
    ask(app)
    expect(app.locator(".bubble.bot")).to_be_visible()
    expect(app.locator(".sources")).to_have_count(0)


def test_not_found_answer_becomes_a_yellow_callout_not_a_card(app):
    mock_stream(app, sse(("token", {"text": "I don't know "}), ("token", {"text": "from uploaded PDF."}), ("sources", {"sources": ["a.pdf"]}), ("done", {})))
    ask(app)
    expect(app.locator(".callout")).to_contain_text("I could not find that in your documents")
    expect(app.locator(".bubble.bot")).to_have_count(0)
    expect(app.locator(".source-chip")).to_have_count(0)


def test_markdown_answer_is_rendered_as_formatted_text(app):
    mock_stream(app, stream_ok("**Handshake** steps:\n\n1. SYN\n2. SYN-ACK\n\nUse `ACK` last."))
    ask(app)
    bot = app.locator(".bubble.bot")
    expect(bot.locator("strong")).to_have_text("Handshake")
    expect(bot.locator("ol > li")).to_have_count(2)
    expect(bot.locator("code")).to_have_text("ACK")
    expect(bot).not_to_contain_text("**")


def test_markdown_links_open_safely_in_a_new_tab(app):
    mock_stream(app, stream_ok("See [the spec](https://example.com/spec)."))
    ask(app)
    link = app.locator(".bubble.bot a")
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")


def test_answer_html_is_sanitized_scripts_handlers_and_js_links_are_removed(app):
    mock_stream(app, stream_ok(
        "ok <img src=x onerror=window.__pwned=1> <script>window.__pwned=2</script> [bad](javascript:window.__pwned=3)"
    ))
    ask(app)
    bot = app.locator(".bubble.bot")
    expect(bot).to_contain_text("ok")
    expect(bot.locator("img, script")).to_have_count(0)
    expect(bot.locator("a[href^='javascript']")).to_have_count(0)
    app.wait_for_timeout(200)
    assert app.evaluate("window.__pwned") is None


def test_partial_markdown_while_streaming_does_not_break_the_page(app, app_env):
    m, _ = app_env
    release = threading.Event()

    def slow(messages):
        yield AIMessageChunk(content="**half bol")
        release.wait(5)
        yield AIMessageChunk(content="d** done")

    m.llm.stream = slow
    ask(app)
    expect(app.locator(".bubble.bot")).to_contain_text("half bol")
    release.set()
    expect(app.locator(".bubble.bot strong")).to_have_text("half bold")


def test_typing_indicator_is_a_small_bubble_not_a_full_width_bar(app):
    app.route("**/api/stream", lambda r: None)  # never answers -> typing stays visible
    ask(app)
    typing = app.locator(".bubble.typing")
    expect(typing).to_be_visible()
    box = typing.bounding_box()
    assert box["width"] < 120, box


def test_prompt_asks_for_english_markdown_answers(app_env):
    m, client = app_env
    client.post("/api", json={"input": "q"})
    system = m.llm.calls[0][0]["content"]
    assert "English" in system and "Markdown" in system


# --- chat: streaming --------------------------------------------------------

def test_tokens_appear_while_the_answer_is_still_streaming(app, app_env):
    m, _ = app_env
    release = threading.Event()

    def slow(messages):
        yield AIMessageChunk(content="Hel")
        release.wait(5)
        yield AIMessageChunk(content="lo")

    m.llm.stream = slow
    ask(app)
    expect(app.locator(".bubble.bot")).to_have_text("Hel")
    expect(app.locator("#send-btn")).to_be_disabled()
    release.set()
    expect(app.locator(".bubble.bot")).to_have_text("Hello")
    expect(app.locator("#send-btn")).to_be_enabled()


def test_real_backend_answer_streams_end_to_end(app):
    ask(app)
    expect(app.locator(".bubble.bot")).to_have_text("fake answer")


def test_falls_back_to_plain_api_when_stream_endpoint_is_unavailable(app):
    app.route("**/api/stream", lambda r: r.fulfill(status=404, json={"detail": "Not Found"}))
    app.route("**/api", lambda r: r.fulfill(json={"content": "Plain answer", "sources": ["a.pdf"]}))
    ask(app)
    expect(app.locator(".bubble.bot")).to_have_text("Plain answer")
    expect(app.locator(".source-chip")).to_have_count(1)


def test_stream_error_event_shows_an_error_callout_and_frees_the_input(app):
    mock_stream(app, sse(("token", {"text": "par"}), ("error", {"detail": "Answer generation failed."})))
    ask(app)
    expect(app.locator(".callout.error")).to_have_text("Answer generation failed.")
    expect(app.locator("#send-btn")).to_be_enabled()


def test_server_error_shows_an_error_callout(app):
    app.route("**/api/stream", lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    app.route("**/api", lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    ask(app)
    expect(app.locator(".callout.error")).to_have_text("boom")
    expect(app.locator("#send-btn")).to_be_enabled()


# --- phone ------------------------------------------------------------------

@pytest.fixture
def phone(page, live_url):
    page.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(body="", content_type="text/css"))
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_url)
    return page


def test_phone_has_no_horizontal_scroll(phone):
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_phone_hides_sidebar_until_files_button_opens_it(phone):
    expect(phone.locator("#side")).to_be_hidden()
    phone.click("#files-toggle")
    expect(phone.locator("#side")).to_be_visible()
    expect(phone.locator("#files-toggle")).to_have_attribute("aria-expanded", "true")
    phone.keyboard.press("Escape")
    expect(phone.locator("#side")).to_be_hidden()


def test_phone_files_button_counts_files(phone):
    mock_upload(phone)
    phone.click("#files-toggle")
    phone.set_input_files("#file-input", TXT)
    phone.click("#upload-btn")
    phone.click("#side-close")
    expect(phone.locator("#files-toggle")).to_contain_text("Files · 1")


def test_phone_touch_targets_are_at_least_44px(phone):
    phone.click("#files-toggle")
    for sel in ("#side-close", "#files-toggle", "#upload-btn", "#attach-btn", "#send-btn"):
        phone.locator(sel).scroll_into_view_if_needed()
    phone.click("#side-close")
    for sel in ("#files-toggle", "#attach-btn", "#send-btn", ".tip"):
        box = phone.locator(sel).first.bounding_box()
        assert box["width"] >= 44 - 0.5 and box["height"] >= 44 - 0.5, sel


def test_phone_composer_text_is_16px_so_ios_does_not_zoom(phone):
    assert phone.evaluate("getComputedStyle(document.getElementById('chat-input')).fontSize") == "16px"


# --- accessibility ----------------------------------------------------------

def test_every_button_and_the_input_has_an_accessible_name(app):
    names = app.evaluate(
        """() => [...document.querySelectorAll('button')].map(b =>
            (b.getAttribute('aria-label') || b.textContent).trim())"""
    )
    assert all(names), names
    expect(app.get_by_label("Your question")).to_be_visible()


def test_keyboard_focus_ring_is_visible_on_send(app):
    app.focus("#send-btn")
    app.keyboard.press("Shift+Tab")
    app.keyboard.press("Tab")
    assert app.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "solid"


def _lum(hex_):
    ch = [int(hex_[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def _ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize(
    "fg, bg",
    [
        ("#1a211e", "#ffffff"), ("#5f6563", "#ffffff"), ("#5f6563", "#eef1f0"),
        ("#1d3e56", "#e1f6fd"), ("#0d3d38", "#ccf3ea"), ("#0d3d38", "#b8eae0"),
        ("#582d1d", "#ffefd6"), ("#4f3422", "#fefbe9"), ("#64172b", "#feeaed"),
        ("#193b2d", "#e6f6eb"), ("#4f3422", "#fff7c2"),
        ("#0d3d38", "#e0f8f3"), ("#1a211e", "#f3fbf9"), ("#5f6563", "#e6e9e8"),
    ],
)
def test_design_token_pairs_meet_wcag_aa_contrast(fg, bg):
    assert _ratio(fg, bg) >= 4.5


# --- session persistence (refresh keeps the uploaded files) -------------------

def kb_cookie(page):
    return next(c for c in page.context.cookies() if c["name"] == "kb_sid")


def test_session_cookie_is_httponly_lax_and_invisible_to_scripts(app):
    cookie = kb_cookie(app)
    assert cookie["httpOnly"] and cookie["sameSite"] == "Lax"
    assert "kb_sid" not in app.evaluate("document.cookie")


def test_refresh_keeps_the_same_session_cookie(app):
    before = kb_cookie(app)["value"]
    app.reload()
    assert kb_cookie(app)["value"] == before


def test_refresh_restores_uploaded_files_in_the_sidebar(app, app_env, live_url):
    m, _ = app_env
    sources = ["notes.txt", "notes.txt", "guide.pdf"]
    m.qdrant.scroll.return_value = ([SimpleNamespace(payload={"metadata": {"source": s}}) for s in sources], None)
    app.reload()
    rows = app.locator(".file-row")
    expect(rows).to_have_count(2)
    expect(rows.nth(0)).to_contain_text("guide.pdf")
    expect(rows.nth(1)).to_contain_text("notes.txt")
    expect(rows.nth(1)).to_contain_text("2 passages")
    expect(rows.locator(".badge")).to_have_count(0)
    expect(app.locator("#file-count")).to_have_text("2 files")
    expect(app.locator("#sheet-sub")).to_contain_text("3 passages in all")


def test_restored_files_come_from_this_browsers_own_session(app, app_env, live_url):
    m, _ = app_env
    m.qdrant.scroll.return_value = ([], None)
    app.reload()
    app.wait_for_load_state("networkidle")
    first = m.qdrant.scroll.call_args.kwargs["scroll_filter"].must[0].match.value
    app.context.clear_cookies()
    app.reload()
    app.wait_for_load_state("networkidle")
    second = m.qdrant.scroll.call_args.kwargs["scroll_filter"].must[0].match.value
    assert first != second and len(first) == 32


def test_empty_session_after_refresh_shows_the_first_visit_state(app):
    app.reload()
    expect(app.locator("#file-count")).to_have_text("0 files")
    expect(app.get_by_role("heading", name="Start with a document, then ask a question.")).to_be_visible()


def test_failed_restore_does_not_break_the_page(app):
    app.route("**/session/files", lambda r: r.fulfill(status=500, json={"detail": "qdrant down"}))
    app.reload()
    expect(app.locator("#file-count")).to_have_text("0 files")
    expect(app.locator("#chat-input")).to_be_enabled()


# --- file viewer ------------------------------------------------------------

def restore_files(app, m, *names):
    m.qdrant.scroll.return_value = ([SimpleNamespace(payload={"metadata": {"source": n}}) for n in names], None)
    app.reload()


def mock_passages(page, passages, status=200, seen=None):
    def handle(route):
        if seen is not None:
            seen.append(route.request.url)
        body = {"source": "notes.txt", "passages": passages} if status == 200 else {"detail": "That file is not in this session"}
        route.fulfill(status=status, json=body)

    page.route("**/session/passages*", handle)


def test_clicking_a_file_opens_a_viewer_with_its_passages(app, app_env):
    m, _ = app_env
    mock_passages(app, [{"n": 1, "text": "First passage"}, {"n": 2, "text": "Second passage"}])
    restore_files(app, m, "notes.txt")
    app.get_by_role("button", name="Open notes.txt").click()
    dialog = app.locator("#viewer")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("heading", name="notes.txt")).to_be_visible()
    expect(dialog.locator("#viewer-meta")).to_have_text("2 passages")
    expect(dialog.locator(".passages li")).to_have_text(["1First passage", "2Second passage"])


def test_viewer_requests_the_clicked_file_by_name(app, app_env):
    m, _ = app_env
    seen = []
    mock_passages(app, [{"n": 1, "text": "x"}], seen=seen)
    restore_files(app, m, "My Notes (v2).md")
    app.get_by_role("button", name="Open My Notes (v2).md").click()
    expect(app.locator(".passages li")).to_have_count(1)
    assert seen[0].endswith("/session/passages?source=My%20Notes%20(v2).md")


def test_viewer_closes_with_button_escape_and_backdrop_and_returns_focus(app, app_env):
    m, _ = app_env
    mock_passages(app, [{"n": 1, "text": "x"}])
    restore_files(app, m, "notes.txt")
    row = app.get_by_role("button", name="Open notes.txt")
    row.click()
    app.click("#viewer-close")
    expect(app.locator("#viewer")).not_to_be_visible()
    row.click()
    app.keyboard.press("Escape")
    expect(app.locator("#viewer")).not_to_be_visible()
    row.click()
    app.mouse.click(5, 5)  # on the backdrop
    expect(app.locator("#viewer")).not_to_be_visible()
    expect(row).to_be_focused()


def test_viewer_shows_the_error_when_the_file_cannot_be_opened(app, app_env):
    m, _ = app_env
    mock_passages(app, [], status=404)
    restore_files(app, m, "notes.txt")
    app.get_by_role("button", name="Open notes.txt").click()
    expect(app.locator("#viewer-body")).to_have_text("That file is not in this session")


def test_viewer_shows_file_text_as_text_never_as_html(app, app_env):
    m, _ = app_env
    mock_passages(app, [{"n": 1, "text": "<img src=x onerror=window.__pwned=1><b>bold</b>"}])
    restore_files(app, m, "notes.txt")
    app.get_by_role("button", name="Open notes.txt").click()
    expect(app.locator(".passages p")).to_have_text("<img src=x onerror=window.__pwned=1><b>bold</b>")
    assert app.evaluate("window.__pwned") is None
    expect(app.locator("#viewer img, #viewer b")).to_have_count(0)


def test_real_backend_passages_roundtrip_in_the_viewer(app, app_env):
    m, _ = app_env
    m.qdrant.scroll.side_effect = [
        ([SimpleNamespace(payload={"metadata": {"source": "notes.txt"}})], None),  # restore
        ([SimpleNamespace(payload={"page_content": "later", "metadata": {"chunk": 1}}),
          SimpleNamespace(payload={"page_content": "earlier", "metadata": {"chunk": 0}})], None),  # viewer
    ]
    app.reload()
    app.get_by_role("button", name="Open notes.txt").click()
    expect(app.locator(".passages p")).to_have_text(["earlier", "later"])


def test_uploaded_file_row_opens_the_viewer_too(app):
    mock_upload(app)
    mock_passages(app, [{"n": 1, "text": "hello"}])
    app.set_input_files("#file-input", TXT)
    app.click("#upload-btn")
    app.get_by_role("button", name="Open notes.txt").click()
    expect(app.locator(".passages p")).to_have_text("hello")


def test_phone_viewer_fits_the_screen(phone, app_env):
    m, _ = app_env
    mock_passages(phone, [{"n": 1, "text": "word " * 200}])
    m.qdrant.scroll.return_value = ([SimpleNamespace(payload={"metadata": {"source": "notes.txt"}})], None)
    phone.reload()
    phone.click("#files-toggle")
    phone.get_by_role("button", name="Open notes.txt").click()
    box = phone.locator("#viewer").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["y"] + box["height"] <= 844
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


# --- chat survives a refresh ------------------------------------------------

def test_refresh_restores_the_conversation_with_sources(app):
    mock_stream(app, stream_ok("**Answer** one", sources=["a.pdf"]))
    ask(app, "First question")
    expect(app.locator(".bubble.bot strong")).to_have_text("Answer")
    app.unroute("**/api/stream")
    app.reload()
    expect(app.locator(".bubble.user")).to_have_text("First question")
    expect(app.locator(".bubble.bot strong")).to_have_text("Answer")
    expect(app.locator(".source-chip")).to_have_count(1)
    expect(app.locator(".empty-state")).to_have_count(0)


def test_restored_chat_gives_follow_up_questions_their_context(app):
    mock_stream(app, stream_ok("Earlier answer"))
    ask(app, "Earlier question")
    expect(app.locator(".bubble.bot")).to_have_text("Earlier answer")
    app.unroute("**/api/stream")
    app.reload()
    expect(app.locator(".bubble.bot")).to_have_text("Earlier answer")
    seen = []
    mock_stream(app, stream_ok("Next"), seen=seen)
    ask(app, "Follow up")
    expect(app.locator(".bubble.bot")).to_have_count(2)
    history = json.loads(seen[0].post_data)["history"]
    assert [h["content"] for h in history] == ["Earlier question", "Earlier answer", "Follow up"]


def test_restored_not_found_answer_is_still_a_callout(app):
    mock_stream(app, sse(("token", {"text": NOT_FOUND}), ("sources", {"sources": []}), ("done", {})))
    ask(app, "Grading?")
    expect(app.locator(".callout")).to_have_count(1)
    app.unroute("**/api/stream")
    app.reload()
    expect(app.locator(".callout")).to_contain_text("I could not find that in your documents")


def test_another_session_does_not_see_this_browsers_chat(app):
    mock_stream(app, stream_ok("Private answer"))
    ask(app, "Private question")
    expect(app.locator(".bubble.bot")).to_be_visible()
    app.unroute("**/api/stream")
    app.context.clear_cookies()  # = a different session
    app.reload()
    expect(app.locator(".bubble.user")).to_have_count(0)
    expect(app.locator(".empty-state")).to_have_count(1)


def test_clear_chat_empties_the_conversation_and_it_stays_empty_after_refresh(app):
    mock_stream(app, stream_ok("Answer"))
    ask(app, "Question")
    expect(app.locator("#clear-chat")).to_be_visible()
    app.click("#clear-chat")
    expect(app.locator(".bubble")).to_have_count(0)
    expect(app.locator(".empty-state")).to_have_count(1)
    expect(app.locator("#clear-chat")).to_be_hidden()
    app.reload()
    expect(app.locator(".bubble")).to_have_count(0)


def test_example_questions_still_work_after_clearing_the_chat(app):
    mock_stream(app, stream_ok("Answer"))
    ask(app, "Question")
    app.click("#clear-chat")
    app.locator(".tip").first.click()
    expect(app.locator("#chat-input")).to_have_value("What are the main points of chapter 3?")


def test_corrupt_saved_chat_does_not_break_the_page(app):
    key = app.evaluate("Object.keys(localStorage).find(k => k.startsWith('kb-chat:')) || null")
    if key is None:
        mock_stream(app, stream_ok("x"))
        ask(app, "q")
        expect(app.locator(".bubble.bot")).to_be_visible()
        expect(app.locator("#send-btn")).to_be_enabled()  # chat is saved when the answer is complete
        key = app.evaluate("Object.keys(localStorage).find(k => k.startsWith('kb-chat:'))")
    app.evaluate("k => localStorage.setItem(k, '{not json')", key)
    app.reload()
    expect(app.locator("#chat-input")).to_be_enabled()
    expect(app.locator(".bubble")).to_have_count(0)


def test_chat_keeps_only_the_most_recent_messages(app):
    mock_stream(app, stream_ok("A"))
    for i in range(25):
        ask(app, f"q{i}")
        expect(app.locator(".bubble.bot")).to_have_count(i + 1)
        expect(app.locator("#send-btn")).to_be_enabled()
    app.unroute("**/api/stream")
    app.reload()
    expect(app.locator(".bubble.user")).to_have_count(20)  # 40 messages = 20 Q/A pairs
    expect(app.locator(".bubble.user").last).to_have_text("q24")
