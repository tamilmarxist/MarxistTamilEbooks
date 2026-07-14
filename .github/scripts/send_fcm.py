#!/usr/bin/env python3
"""Send an FCM topic notification for the newest book in booksdb.json.

Run by the GitHub Action (notify.yml) after booksdb.json changes on a push.
It reads the newest book (books[0]), mints a short-lived OAuth2 access token
from the Firebase service-account key (provided via the FCM_SERVICE_ACCOUNT
env var, which is a GitHub Actions secret), and POSTs to the FCM HTTP v1 API
for the topic `new_book_added`.

Duplicate-guard: if a previous copy of booksdb.json is provided via
PREV_BOOKSDB and its newest bookid is unchanged, nothing is sent. This stops
re-publishes / metadata edits of the same issue from re-notifying users.

Environment:
    FCM_SERVICE_ACCOUNT  Firebase service-account JSON (raw string). Required.
    BOOKSDB              Path to booksdb.json. Default: booksdb.json
    PREV_BOOKSDB        Path to the previous booksdb.json (optional).
    TOPIC               FCM topic. Default: new_book_added
    DRY_RUN             If set to "1", build the message but do not send.
"""

from __future__ import annotations

import json
import os
import sys

# google-auth and requests are imported lazily inside the functions that send,
# so the dry-run / duplicate-guard paths work without those libraries present.

SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]

# Notification copy. Title is a fixed Tamil string ("A new issue has been
# released"); the body is the issue's own Tamil month-year title.
NOTIF_TITLE = "புதிய இதழ் வெளியானது"


def _load_books(path: str) -> list:
    """Return the books list from a booksdb.json file, or [] if unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    books = data.get("books")
    return books if isinstance(books, list) else []


def _newest_bookid(path: str) -> str | None:
    books = _load_books(path)
    if books and isinstance(books[0], dict):
        return books[0].get("bookid")
    return None


def _access_token(sa_info: dict) -> str:
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=SCOPES
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def main() -> int:
    raw_sa = os.environ.get("FCM_SERVICE_ACCOUNT", "").strip()
    if not raw_sa:
        print("::error::FCM_SERVICE_ACCOUNT is not set.", file=sys.stderr)
        return 1
    try:
        sa_info = json.loads(raw_sa)
    except ValueError as exc:
        print(f"::error::FCM_SERVICE_ACCOUNT is not valid JSON: {exc}", file=sys.stderr)
        return 1

    project_id = sa_info.get("project_id")
    if not project_id:
        print("::error::Service account JSON has no project_id.", file=sys.stderr)
        return 1

    booksdb = os.environ.get("BOOKSDB", "booksdb.json")
    topic = os.environ.get("TOPIC", "new_book_added")

    books = _load_books(booksdb)
    if not books:
        print(f"No books found in {booksdb}; nothing to notify.")
        return 0
    book = books[0]

    # Duplicate guard: skip if the newest book is unchanged from the previous
    # commit's booksdb.json (re-publish or metadata-only edit).
    prev_path = os.environ.get("PREV_BOOKSDB")
    if prev_path and os.path.exists(prev_path):
        if _newest_bookid(prev_path) == book.get("bookid"):
            print(
                f"Newest bookid unchanged ({book.get('bookid')}); "
                "skipping notification."
            )
            return 0

    title = book.get("title", "")
    bookid = book.get("bookid", "")
    image = book.get("image", "")
    epub = book.get("epub", "")

    message = {
        "message": {
            "topic": topic,
            "notification": {
                "title": NOTIF_TITLE,
                "body": title,
            },
            # Data payload for deep-linking on tap (handled by the app's
            # FirebaseMessagingService). All values must be strings.
            "data": {
                "bookid": str(bookid),
                "title": str(title),
                "epub": str(epub),
                "image": str(image),
            },
            "android": {
                "priority": "high",
                "notification": {
                    # Big-picture style; ignored if the URL is empty/unreachable.
                    "image": str(image),
                },
            },
        }
    }

    if os.environ.get("DRY_RUN") == "1":
        print("DRY_RUN=1 — message that would be sent:")
        print(json.dumps(message, ensure_ascii=False, indent=2))
        return 0

    token = _access_token(sa_info)
    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; UTF-8",
        },
        data=json.dumps(message, ensure_ascii=False).encode("utf-8"),
        timeout=30,
    )

    if resp.status_code == 200:
        name = resp.json().get("name", "")
        print(f"Sent notification for “{title}” to topic '{topic}'. ({name})")
        return 0

    print(
        f"::error::FCM send failed: {resp.status_code} {resp.text}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
