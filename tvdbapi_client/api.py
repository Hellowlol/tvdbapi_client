import json
import logging

import cachecontrol
from cachecontrol import caches
from oslo.config import cfg
import requests
import six

from tvdbapi_client import timeutil

LOG = logging.getLogger(__name__)

cfg.CONF.import_opt('apikey', 'tvdbapi_client.options')
cfg.CONF.import_opt('username', 'tvdbapi_client.options')
cfg.CONF.import_opt('userpass', 'tvdbapi_client.options')
cfg.CONF.import_opt('service_url', 'tvdbapi_client.options')
cfg.CONF.import_opt('verify_ssl_certs', 'tvdbapi_client.options')
cfg.CONF.import_opt('select_first', 'tvdbapi_client.options')

DEFAULT_HEADERS = {
    'Accept-Language': 'en',
    'Content-Type': 'application/json',
    }


def requires_auth(f):
    """Handles authentication checks.

    .. py:decorator:: requires_auth

        Checks if the token has expired and performs authentication if needed.
    """
    @six.wraps(f)
    def wrapper(self, *args, **kwargs):
        if self.token_expired:
            self.authenticate()
        return f(self, *args, **kwargs)
    return wrapper


class TVDBClient(object):

    def __init__(self, apikey=None, username=None, userpass=None):
        """TVDB Api client

        :param str apikey: apikey from thetvdb
        :param str username: username used on thetvdb
        :param str userpass: password used on thetvdb
        """
        self.__apikey = apikey or cfg.CONF.apikey
        self.__username = username or cfg.CONF.username
        self.__userpass = userpass or cfg.CONF.userpass
        self.__token = None

        self._token_timer = None
        self._session = None
        self._headers = DEFAULT_HEADERS
        self._language = 'en'

    @property
    def headers(self):
        """Provides access to updated headers."""
        self._headers.update(**{'Accept-Language': self.language})
        if self.__token:
            self._headers.update(
                **{'Authorization': 'Bearer %s' % self.__token})
        return self._headers

    @property
    def language(self):
        """Provides access to current language."""
        return self._language

    @language.setter
    def language(self, abbr):
        self._language = abbr

    @property
    def token_expired(self):
        """Provides access to flag indicating if token has expired."""
        if self._token_timer is None:
            return True
        return timeutil.is_newer_than(self._token_timer, timeutil.ONE_HOUR)

    @property
    def session(self):
        """Provides access to request session with local cache enabled."""
        if self._session is None:
            self._session = cachecontrol.CacheControl(
                requests.Session(),
                cache=caches.FileCache('.tvdb_cache'))
        return self._session

    def _exec_request(self, service, method=None, path_args=None, data=None,
                      params=None):
        """Execute request"""

        if path_args is None:
            path_args = []

        req = {
            'method': method or 'get',
            'url': '/'.join(str(a).strip('/') for a in [cfg.CONF.service_url,
                                                        service] + path_args),
            'data': json.dumps(data) if data else None,
            'headers': self.headers,
            'params': params,
            'verify': cfg.CONF.verify_ssl_certs,
            }

        try:
            resp = self.session.request(**req)
        except requests.exceptions.RequestException:
            # request failed; not much we can do
            LOG.exception('api failed (%s %s)', req['method'], req['url'])
            raise

        resp.raise_for_status()
        return resp.json() if resp.text else resp.text

    def _login(self):
        data = {'apikey': self.__apikey,
                'username': self.__username,
                'userpass': self.__userpass,
                }
        return self._exec_request('login', method='post', data=data)

    def _refresh_token(self):
        return self._exec_request('refresh_token')

    def authenticate(self):
        """Aquires authorization token for using thetvdb apis."""
        if self.__token:
            try:
                resp = self._refresh_token()
            except requests.exceptions.HTTPError as err:
                # if a 401 is the cause try to login
                if err.response.status_code == 401:
                    resp = self._login()
                else:
                    raise
        else:
            resp = self._login()

        self.__token = resp.get('token')
        self._token_timer = timeutil.utcnow()

    @requires_auth
    def search_series(self, name=None, imdbid=None, zap2itid=None):
        """Provides the ability to search for a series.

        .. warning::

            authorization token required

        :param str name: name of series
        :param str imdbid: IMDB id
        :param str zap2itid: zap2it id
        :returns: series record or series records
        :rtype: dict
        """
        params = {}
        if name:
            params['name'] = name
        if imdbid:
            params['imdbId'] = imdbid
        if zap2itid:
            params['zap2itId'] = zap2itid
        resp = self._exec_request(
            'search', path_args=['series'], params=params)
        if cfg.CONF.select_first:
            return resp['data'][0]
        return resp['data']

    @requires_auth
    def get_series(self, series_id):
        """Retrieve series record.

        .. warning::

            authorization token required

        :param str series_id: id of series as found on thetvdb
        :returns: series record
        :rtype: dict
        """
        return self._exec_request('series', path_args=[series_id])['data']

    @requires_auth
    def get_episodes(self, series_id, page=None):
        """All episodes for a given series.

        Paginated with 100 results per page.

        .. warning::

            authorization token required

        :param str series_id: id of series as found on thetvdb
        :param int page: Page of results to fetch.
                         Defaults to page 1 if not provided.
        :returns: series episode records
        :rtype: list
        """
        params = {}
        if page:
            params['page'] = page
        return self._exec_request(
            'series', path_args=[series_id, 'episodes'], params=params)['data']

    @requires_auth
    def get_episodes_summary(self, series_id):
        """Returns a summary of the episodes and seasons for the series.

        .. warning::

            authorization token required

        .. note::

            Season "0" is for all episodes that are considered to be specials.

        :param str series_id: id of series as found on thetvdb
        :returns: summary of the episodes and seasons for the series
        :rtype: dict
        """
        return self._exec_request(
            'series', path_args=[series_id, 'episodes', 'summary'])['data']

    @requires_auth
    def get_series_image_info(self, series_id):
        """Returns a summary of the images for a particular series

        .. warning::

            authorization token required

        :param str series_id: id of series as found on thetvdb
        :returns: summary of the images for the series
        :rtype: dict
        """
        return self._exec_request(
            'series', path_args=[series_id, 'images'])['data']

    @requires_auth
    def get_episode(self, episode_id):
        """Returns the full information for a given episode id.

        .. warning::

            authorization token required

        :param str episode_id: id of episode as found on thetvdb
        :returns: episode record
        :rtype: dict
        """
        return self._exec_request('episodes', path_args=[episode_id])['data']