#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import time
import json
import gzip
import argparse
import requests
from datetime import date, timedelta

try:
    import osrc  # NOQA
except ImportError:
    import sys
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
finally:
    from osrc.models import db, Event
    from osrc.process import process_repo, process_user, parse_datetime
    from osrc import create_app


# The URL template for the GitHub Archive.
archive_url = ("http://data.githubarchive.org/"
               "{year}-{month:02d}-{day:02d}-{n}.json.gz")


def process_one(fh):
    strt = time.time()
    count = 0
    with gzip.GzipFile(fileobj=fh) as f:
        for line in f:
            parse_event(json.loads(line.decode("utf-8")))
            count += 1
    db.session.commit()
    print("... processed {0} events in {1} seconds"
          .format(count, time.time() - strt))


def parse_event(event):
    # Parse the standard elements.
    _process_event(event)

    # Parse any event specific elements.
    parser = event_types.get(event["type"], None)
    if parser is not None:
        parser(event["payload"])


def _process_event(event):
    q = Event.query.filter(Event.id == event["id"])
    if q.first() is not None:
        return
    user = process_user(event["actor"])
    repo = process_repo(event["repo"])
    dt = parse_datetime(event["created_at"])
    db.session.add(Event(
        id=event["id"],
        event_type=event["type"],
        datetime=dt,
        day=dt.weekday(),
        hour=dt.hour,
        user=user,
        repo=repo,
    ))


def _process_fork(payload):
    process_repo(payload["forkee"])


def _process_pull_request(payload):
    process_repo(payload["pull_request"]["base"]["repo"])


def _process_pull_request_comment(payload):
    _process_pull_request(payload)


event_types = dict(
    ForkEvent=_process_fork,
    PullRequestEvent=_process_pull_request,
    PullRequestReviewCommentEvent=_process_pull_request_comment,
)


if __name__ == "__main__":
    dirname = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_settings = os.path.join(dirname, "local.py")

    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*",
                        help="files to process, otherwise downloaded")
    parser.add_argument("-s", "--since", default=None,
                        help="the starting date YYYY-MM-DD")
    parser.add_argument("-f", "--filename",
                        default=default_settings,
                        help="a Python file with the app settings")
    args = parser.parse_args()

    app = create_app(os.path.abspath(args.filename))
    with app.app_context():
        try:
            if len(args.files):
                for fn in args.files:
                    print("Processing: {0}".format(fn))
                    process_one(open(fn, "rb"))
            else:
                today = date.today()
                if args.since is None:
                    since = today - timedelta(1)
                else:
                    since = date(**dict(zip(["year", "month", "day"],
                                        map(int, args.since.split("-")))))

                while since < today:
                    base_date = {"year": since.year, "month": since.month,
                                 "day": since.day}
                    urls = (archive_url.format(**(dict(base_date, n=n)))
                            for n in range(24))
                    for n in range(24):
                        url = archive_url.format(**(dict(base_date, n=n)))
                        print("Processing: {0}".format(url))
                        r = requests.get(url, stream=True)
                        r.raise_for_status()
                        process_one(r.raw)

                    since += timedelta(1)

        except:
            raise

        finally:
            db.session.commit()