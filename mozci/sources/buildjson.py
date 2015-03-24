#!/usr/bin/env python
"""
This module helps with the buildjson data generated by the Release Engineering
systems: http://builddata.pub.build.mozilla.org/builddata/buildjson
"""
import json
import logging

from mozci.utils.tzone import utc_dt, utc_time, utc_day
from mozci.utils.transfer import fetch_file

LOG = logging.getLogger()

BUILDJSON_DATA = "http://builddata.pub.build.mozilla.org/builddata/buildjson"
BUILDS_4HR_FILE = "builds-4hr.js"
BUILDS_DAY_FILE = "builds-%s.js"

# This helps us read into memory and load less from disk
BUILDS_DAY_INDEX = {}


def _fetch_file(filename):
    '''
    Helper method to download files.

    This function caches the uncompressed gzip files requested in the past.

    Returns all jobs inside of this buildjson file.
    '''
    url = "%s/%s.gz" % (BUILDJSON_DATA, filename)
    # If the file exists and is valid we won't download it again
    fetch_file(filename, url)

    LOG.debug("About to load %s." % filename)
    builds = json.load(open(filename))["builds"]
    return builds


def _find_job(request_id, jobs, loaded_from):
    """
    Look for request_id in a list of jobs.
    loaded_from is simply to indicate where those jobs were loaded from.
    """
    found = None
    LOG.debug("We are going to look for %s in %s." % (request_id, loaded_from))

    for job in jobs:
        # XXX: Issue 104 - We have an unclear source of request ids
        prop_req_ids = job["properties"].get("request_ids", [])
        root_req_ids = job["request_ids"]
        if request_id in list(set(prop_req_ids + root_req_ids)):
            LOG.debug("Found %s" % str(job))
            found = job
            return job

    return found


def query_job_data(complete_at, request_id):
    """
    Look for a job identified by `request_id` inside of a buildjson
    file under the "builds" entry.

    Through `complete_at`, we can determine on which day we can find the
    metadata about this job.

    raises Exception when we can't find the job.

    WARNING: "request_ids" and the ones from "properties" can differ. Issue filed.

    If found, the returning entry will look like this (only important values
    are referenced):

    .. code-block:: python

        {
            "builder_id": int, # It is a unique identifier of a builder
            "starttime": int,
            "endtime": int,
            "properties": {
                "blobber_files": json, # Mainly applicable to test jobs
                "buildername": string,
                "buildid": string,
                "log_url", string,
                "packageUrl": string, # It only applies for build jobs
                "revision": string,
                "repo_path": string, # e.g. projects/cedar
                "request_ids": list of ints, # Scheduling ID
                "slavename": string, # e.g. t-w864-ix-120
                "symbolsUrl": string, # It only applies for build jobs
                "testsUrl": string,   # It only applies for build jobs
            },
            "request_ids": list of ints, # Scheduling ID
            "requesttime": int,
            "result": int, # Job's exit code
            "slave_id": int, # Unique identifier for the machine that run it
        }

    NOTE: Remove this block once https://bugzilla.mozilla.org/show_bug.cgi?id=1135991
    is fixed.

    There is so funkiness in here. A buildjson file for a day is produced
    every 15 minutes all the way until midnight pacific time. After that, a new
    _UTC_ day commences. However, we will only contain all jobs ending within the
    UTC day and not the PT day. If you run any of this code in the last 4 hours of
    the pacific day, you will have a gap of 4 hours for which you won't have buildjson
    data (between 4-8pm PT). The gap starts appearing after 8pm PT when builds-4hr
    cannot cover it.

    If we look all endtime values on a day and we print the minimum and maximues values,
    this is what we get:

    .. code-block:: python

        1424649600 Mon, 23 Feb 2015 00:00:00  () Sun, 22 Feb 2015 16:00:00 -0800 (PST)
        1424736000 Tue, 24 Feb 2015 00:00:00  () Mon, 23 Feb 2015 16:00:00 -0800 (PST)

    This means that since 4pm to midnight we generate the same file again and again
    without adding any new data.
    """
    assert type(request_id) is int
    assert type(complete_at) is int

    global BUILDS_DAY_INDEX

    date = utc_day(complete_at)
    LOG.debug("Job identified with complete_at value: %d run on %s UTC." % (complete_at, date))

    then = utc_dt(complete_at)
    hours_ago = (utc_dt() - then).total_seconds() / (60 * 60)
    LOG.debug("The job completed at %s (%d hours ago)." % (utc_time(complete_at), hours_ago))

    # If it has finished in the last 4 hours
    if hours_ago < 4:
        # We might be able to grab information about pending and running jobs
        # from builds-running.js and builds-pending.js
        job = _find_job(request_id, _fetch_file(BUILDS_4HR_FILE), BUILDS_4HR_FILE)
    else:
        filename = BUILDS_DAY_FILE % date
        if utc_day() == date:
            # XXX: We could read from memory if we tracked last modified time
            # in BUILDS_DAY_INDEX
            job = _find_job(request_id, _fetch_file(filename), filename)
        else:
            if date in BUILDS_DAY_INDEX:
                LOG.debug("%s is loaded on memory; reading from there." % date)
            else:
                # Let's load the jobs into memory
                jobs = _fetch_file(filename)
                BUILDS_DAY_INDEX[date] = jobs

            job = _find_job(request_id, BUILDS_DAY_INDEX[date], filename)

    if job:
        return job

    raise Exception(
        "We have not found the job. If you see this problem please grep "
        "in %s for %d and run again with --debug and --dry-run. If you report "
        "this issue please upload the mentioned file somewhere for "
        "inspection. Thanks!" % (filename, request_id)
    )
