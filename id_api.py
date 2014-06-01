#!/usr/bin/env python
"""I chose gevent because the awesome gevent.Timeout functionality
makes it incredibly simple to limit the total time a greenlet can run.
In go, to limit the time a http request may take we have to deal with
two separate timeouts.

However, now that we don't actually limit the greenlets themselves,
but rather let them run in the background so the data may be
available for the next request, go would have been a perfect option.
"""

import os
import gevent
import gevent.monkey
gevent.monkey.patch_all()
import requests
from requests import RequestException
from flask import Flask, request, Response
from werkzeug.exceptions import BadRequest
import logbook
import redis
import rippletxt


log = logbook.Logger(__name__)

defaults = {
    'RIPPLE_REST': 'https://rippled.undulous.com',
    'REDIS_URL': None,
    'REDISTOGO_URL': None,
    'SENTRY_DSN': None,
    'LOG_LEVEL': 'INFO',
    'DISABLE_SSL_VERIFY': None,
}

# Cache values retrieved from sources for this many seconds
CACHE_TIMEOUT = 3600 * 12


# Some well-known addresses
ADDRESS_DB = {
    'rfYv1TXnwgDDK4WQNbFALykYuEBnrR4pDX': 'Dividend Rippler',
    'rNPRNzBB92BVpAhhZr4iXDTveCgV5Pofm9': 'Ripple Israel',
    'r3ADD8kXSUKHd6zTCKfnKT3zV9EZHjzp1S': 'Ripple Union',
    'rvYAfWj5gh67oV6fW32ZzP3Aw4Eubs59B': 'Bitstamp',
    'razqQKzJRdB4UxFPWf5NEpEG3WMkmwgcXA': 'RippleChina',
    'rnuF96W4SZoCJmbHYBFoJZpR8eCaxNvekK': 'RippleCN',
    'rJHygWcTLVpSXkowott6kzgZU6viQSVYM1': 'Justcoin',
    'rGDWKWni6exeneJdNbEZ3nVX3Rrw5VG1p1': 'Goodwill LETS',
    'rMwjYedjc7qqtKYVLiAccJSmCwih4LnE2q': 'SnapSwap',
    'ra9eZxMbJrUcgV8ui7aPc161FgrqWScQxV': 'Peercover'
}


app = Flask(__name__)

# Read configuration from environment
config = defaults.copy()
for name in defaults:
    config[name] = os.environ.get(name, config[name])


# Setup logging
log.level = getattr(logbook, config['LOG_LEVEL'] or 'INFO')
handler = logbook.StderrHandler()
handler.format_string = (
    u'[{record.time:%Y-%m-%d %H:%M}] '
    u'{record.level_name}: {record.extra[address]}: {record.message}')
handler.push_application()

# Set up error reporting
if config['SENTRY_DSN']:
    from raven.contrib.flask import Sentry
    sentry = Sentry(app, dsn=config['SENTRY_DSN'])

# Connect to a cache
redis_url = config['REDIS_URL'] or config['REDISTOGO_URL']
if redis_url:
    redis_cache = redis.StrictRedis.from_url(redis_url)
else:
    print('Warning: Running without redis cache, REDIS_URL missing')
    class FakeRedis(object):
        def get(self, *a, **kw):
            return None
        def setex(self, *a, **kw):
            return None
    redis_cache = FakeRedis()


@app.route('/')
def help():
    return Response("""
Info: https://github.com/miracle2k/ripple-id

{host}<address>[?timeout=<int>]
""".format(host=request.host_url).strip(), mimetype='text/plain')


@app.route('/<address>')
def api_any_name(address):
    try:
        timeout = min(float(request.values.get('timeout', 2.0)), 10)
    except ValueError:
        raise BadRequest()
    res = get_any_name(address, timeout)
    return Response(res, mimetype='text/plain')


def get_domain(address):
    """Return a validated-domain for this address, and an x-name if
    one is defined in ripple.txt.

    Uses a ripple-rest server to query the account information.
    """
    response = requests.get("{host}/v1/accounts/{address}/settings".format(
            address=address, host=config['RIPPLE_REST']),
        verify=not bool(config['DISABLE_SSL_VERIFY']))
    if response.status_code != 200:
        log.debug("ripple-rest http request failed: %s" % response.status_code)
        return
    if not response.json()['success']:
        log.debug("ripple-rest returned failure code")
        return

    domain = response.json()["settings"].get("domain")
    if not domain:
        # This ripple address has neither domain nor x-name
        log.debug("address has no domain")
        return "", ""

    # Validate the domain by checking ripple.txt
    possible_temp_error = False
    for txt in rippletxt.get_urls(domain):
        try:
            response = requests.get(txt)
            possible_temp_error = response.status_code != 404
            if response.status_code != 200:
                continue
        except RequestException:
            continue

        break
    else:
        # No ripple.txt found.
        # In some cases we will try again next time.
        if possible_temp_error:
            return None
        else:
            return "", ""

    cfg = rippletxt.loads(response.text)
    if not address in cfg.get('accounts', []):
        # ripple.txt does not advertise this account
        return "", ""

    return domain, cfg.get('x-name', '')


def get_nickname(address):
    """Check if there is a nickname associated with this address,
    by checking id.ripple.com.
    """
    response = requests.get("https://id.ripple.com/v1/user/{address}".format(
        address=address))
    if response.status_code != 200:
        return None
    data = response.json()
    nickname = data.get('username', '')
    return '~%s' % nickname if nickname else ''


def get_any_name(address, timeout=2):
    """Check all known data sources, return the best one available.
    """
    # Locally-known names override everything
    if address in ADDRESS_DB:
        return ADDRESS_DB[address]

    result = {}
    gevent.joinall([
        run_address_resolver(cachify(get_domain, result, 'domain'), address),
        run_address_resolver(cachify(get_nickname, result, 'nickname'), address)
    ], timeout=timeout)

    # pick the best result
    for key in ['name', 'nickname', 'domain']:
        if result.get(key):
            return result[key]
    return ''


tuplify = lambda v: v if isinstance(v, tuple) else (v,)


def run_address_resolver(func, address):
    """Run an address resolver in a greenlet."""
    def inject_address(record):
        record.extra['address'] = address
    def wrapped(address):
        with logbook.Processor(inject_address).threadbound():
            return func(address)
    return gevent.spawn(wrapped, address)


def cachify(func, channel, key):
    """Function decorator that will:

    - Store the return value of the function in the dict
       ``channel`` as ``key`` (i.e. it greenlet-ifies the
        function, since greenlets cannot have a return value
        per se).

    - Uses a redis cache (checks for the value beforehand,
       will write the value to the cache after).

    The function may return multiple values and key may
    be a tuple for htis case.
    """
    key = tuplify(key)
    def wrapped(address):
        # Check the cache first
        cached_result = {}
        for k in key:
            v = redis_cache.get('%s:%s' % (address, k))
            if v is not None:
                cached_result[k] = v
        log.debug("cached result for %s: %s" % (key, cached_result))
        if cached_result:
            channel.update(cached_result)
            return

        # Run the function
        result = func(address)
        log.debug('func result for %s: %s' % (key, result))
        if result is None:
            # None indicates no values available right now,
            # but try again next time rather than caching.
            return
        result = tuplify(result)
        for k, v in zip(key, result):
            channel[k] = v
            redis_cache.setex('%s:%s' % (address, k), CACHE_TIMEOUT, v)
    return wrapped


if __name__ == '__main__':
    app.debug = True
    app.run()