import os
import datetime
import collections
import requests
import time
import re

from pylons import config
import ga_model

log = __import__('logging').getLogger(__name__)

FORMAT_MONTH = '%Y-%m'
MIN_VIEWS = 50
MIN_VISITS = 20


class DownloadAnalytics(object):
    '''Downloads and stores analytics info'''

    def __init__(self, service=None, token=None, profile_id=None, delete_first=False,
                 stat=None, print_progress=False):
        self.period = config['ga-report.period']
        self.service = service
        self.profile_id = profile_id
        self.delete_first = delete_first
        self.stat = stat
        self.token = token
        self.print_progress = print_progress

    def specific_year(self, year):
        for x in range(1,13):
            time_period = str(year) + "-" + str(x)
            for_date = datetime.datetime.strptime(time_period, '%Y-%m')
            try:
                self.specific_month(for_date)
            except:
                pass

    def specific_year_month(self, year, month):
        for x in range(int(month), 13):
            time_period = str(year) + "-" + str(x)
            for_date = datetime.datetime.strptime(time_period, '%Y-%m')
            try:
                self.specific_month(for_date)
            except:
                pass

    def specific_year_step(self, year):
        for x in range(1,13):
            print "Would you like to download data for month: " + str(x) + ". y/n?"
            user_choice = raw_input().lower()
            if user_choice == "y":
                time_period = str(year) + "-" + str(x)
                for_date = datetime.datetime.strptime(time_period, '%Y-%m')
                try:
                    self.specific_month(for_date)
                except:
                    pass
            else:
                pass

    def specific_month(self, date):
        import calendar

        first_of_this_month = datetime.datetime(date.year, date.month, 1)
        _, last_day_of_month = calendar.monthrange(int(date.year), int(date.month))
        last_of_this_month = datetime.datetime(date.year, date.month, last_day_of_month)
        # if this is the latest month, note that it is only up until today
        now = datetime.datetime.now()
        if now.year == date.year and now.month == date.month:
            last_day_of_month = now.day
            last_of_this_month = now
        periods = ((date.strftime(FORMAT_MONTH),
                    last_day_of_month,
                    first_of_this_month, last_of_this_month),)
        self.download_and_store(periods)

    def specific_month_only(self, date, only):
        import calendar

        first_of_this_month = datetime.datetime(date.year, date.month, 1)
        _, last_day_of_month = calendar.monthrange(int(date.year), int(date.month))
        last_of_this_month = datetime.datetime(date.year, date.month, last_day_of_month)
        # if this is the latest month, note that it is only up until today
        now = datetime.datetime.now()
        if now.year == date.year and now.month == date.month:
            last_day_of_month = now.day
            last_of_this_month = now
        periods = ((date.strftime(FORMAT_MONTH),
                    last_day_of_month,
                    first_of_this_month, last_of_this_month),)
        self.download_and_store(periods, only)

    def latest(self):
        if self.period == 'monthly':
            # from first of this month to today
            now = datetime.datetime.now()
            first_of_this_month = datetime.datetime(now.year, now.month, 1)
            periods = ((now.strftime(FORMAT_MONTH),
                        now.day,
                        first_of_this_month, now),)
        else:
            raise NotImplementedError
        self.download_and_store(periods)


    def for_date(self, for_date):
        assert isinstance(for_date, datetime.datetime)
        periods = [] # (period_name, period_complete_day, start_date, end_date)
        if self.period == 'monthly':
            year = for_date.year
            month = for_date.month
            now = datetime.datetime.now()
            first_of_this_month = datetime.datetime(now.year, now.month, 1)
            while True:
                first_of_the_month = datetime.datetime(year, month, 1)
                if first_of_the_month == first_of_this_month:
                    periods.append((now.strftime(FORMAT_MONTH),
                                    now.day,
                                    first_of_this_month, now))
                    break
                elif first_of_the_month < first_of_this_month:
                    in_the_next_month = first_of_the_month + datetime.timedelta(40)
                    last_of_the_month = datetime.datetime(in_the_next_month.year,
                                                           in_the_next_month.month, 1)\
                                                           - datetime.timedelta(1)
                    periods.append((now.strftime(FORMAT_MONTH), 0,
                                    first_of_the_month, last_of_the_month))
                else:
                    # first_of_the_month has got to the future somehow
                    break
                month += 1
                if month > 12:
                    year += 1
                    month = 1
        else:
            raise NotImplementedError
        self.download_and_store(periods)

    @staticmethod
    def get_full_period_name(period_name, period_complete_day):
        if period_complete_day:
            return period_name + ' (up to %ith)' % period_complete_day
        else:
            return period_name

    def download_and_store(self, periods, only='all'):
        for period_name, period_complete_day, start_date, end_date in periods:
            log.info('Period "%s" (%s - %s)',
                     self.get_full_period_name(period_name, period_complete_day),
                     start_date.strftime('%Y-%m-%d'),
                     end_date.strftime('%Y-%m-%d'))

            if self.delete_first:
                log.info('Deleting existing Analytics for this period "%s"',
                         period_name)
                ga_model.delete(period_name, only)

            if self.stat in (None, 'url') and (only == 'all' or only == 'datasets' or only == 'publishers'):
                # Clean out old url data before storing the new
                if only == 'datasets':
                    ga_model.pre_update_url_stats(period_name)

                accountName = config.get('googleanalytics.account')

                path_prefix = '~'  # i.e. it is a regex
                # Possibly there is a domain in the path.
                # I'm not sure why, but on the data.gov.uk property we see
                # the domain gets added to the GA path. e.g.
                #   '/data.gov.uk/data/search'
                #   '/co-prod2.dh.bytemark.co.uk/apps/test-app'
                # but on other properties we don't. e.g.
                #   '/data/search'
                path_prefix += '(/%s)?' % accountName

                if only == 'datasets' or only == 'all':
                    log.info('Downloading analytics for dataset views')
                    data = self.download(start_date, end_date,
                                     path_prefix + '/dataset/[a-z0-9-_]+')

                    log.info('Storing dataset views (%i rows)', len(data.get('url')))
                    self.store(period_name, period_complete_day, data, )

                if only == 'publishers' or only == 'all':
                    log.info('Downloading analytics for publisher views')
                    data = self.download(start_date, end_date,
                                     path_prefix + '/publisher/[a-z0-9-_]+')

                    log.info('Storing publisher views (%i rows)', len(data.get('url')))
                    self.store(period_name, period_complete_day, data,)

                # Create the All records
                if only == 'publishers' or only == 'all':
                    ga_model.post_update_url_stats()

                if only == 'publishers' or only == 'all':
                    log.info('Associating datasets with their publisher')
                    ga_model.update_publisher_stats(period_name) # about 30 seconds.

            if self.stat == 'url-all':
                # This stat is split off just for test purposes
                ga_model.post_update_url_stats()

            if self.stat in (None, 'sitewide') and (only == 'all' or only == 'sitewide'):
                # Clean out old ga_stats data before storing the new
                ga_model.pre_update_sitewide_stats(period_name)

                log.info('Downloading and storing analytics for site-wide stats')
                self.sitewide_stats(period_name, period_complete_day)

            # if self.stat in (None, 'social'):
            #     # Clean out old ga_stats data before storing the new
            #     ga_model.pre_update_social_stats(period_name)

            #     log.info('Downloading and storing analytics for social networks')
            #     self.update_social_info(period_name, start_date, end_date)

    def update_social_info(self, period_name, start_date, end_date):
        start_date = start_date.strftime('%Y-%m-%d')
        end_date = end_date.strftime('%Y-%m-%d')
        query = 'ga:hasSocialSourceReferral=~Yes$'
        metrics = 'ga:entrances'
        sort = '-ga:entrances'

        try:
            args = dict(ids='ga:' + self.profile_id,
                        filters=query,
                        metrics=metrics,
                        sort=sort,
                        dimensions="ga:landingPagePath,ga:socialNetwork",
                        max_results=10000)

            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        data = collections.defaultdict(list)
        rows = results.get('rows')
	count = 0
        for row in rows:
            url = strip_off_host_prefix(row[0])
            data[url].append((row[1], int(row[2]),))
	    count = count + 1
            if count == 100:
                break
	data = self._edit_url(data) 
        ga_model.update_social(period_name, data)

    def download(self, start_date, end_date, path=None):
        '''Get views & visits data for particular paths & time period from GA
        '''
        start_date = start_date.strftime('%Y-%m-%d')
        end_date = end_date.strftime('%Y-%m-%d')
        query = 'ga:pagePath=%s$' % path
        metrics = 'ga:pageviews, ga:visits'

        # Supported query params at
        # https://developers.google.com/analytics/devguides/reporting/core/v3/reference
        try:
            args = {}
            args["sort"] = "-ga:pageviews"
            args["max-results"] = 100000
            args["dimensions"] = "ga:pagePath"
            args["start-date"] = start_date
            args["end-date"] = end_date
            args["metrics"] = metrics
            args["ids"] = "ga:" + self.profile_id
            args["filters"] = query
            args["alt"] = "json"

            results = self._get_ga_data(args)

        except Exception, e:
            log.exception(e)
            return dict(url=[])

        packages = []
        log.info('There are %d results', results['totalResults'])
        for entry in results.get('rows'):
            (path, pageviews, visits) = entry
            url = strip_off_host_prefix(path)  # strips off domain e.g. www.data.gov.uk or data.gov.uk

            if not url.startswith('/dataset/') and not url.startswith('/publisher/'):
                # filter out strays like:
                # /data/user/login?came_from=http://data.gov.uk/dataset/os-code-point-open
                # /403.html?page=/about&from=http://data.gov.uk/publisher/planning-inspectorate
                continue
            packages.append( (url, pageviews, visits,) ) # Temporary hack
        return dict(url=packages)

    def store(self, period_name, period_complete_day, data):
        if 'url' in data:
            ga_model.update_url_stats(period_name, period_complete_day, data['url'],
                                      print_progress=self.print_progress)

    def sitewide_stats(self, period_name, period_complete_day):
        import calendar
        year, month = period_name.split('-')
        _, last_day_of_month = calendar.monthrange(int(year), int(month))

        start_date = '%s-01' % period_name
        end_date = '%s-%s' % (period_name, last_day_of_month)
        funcs = ['_page_stats','_totals_stats', '_os_stats',
                 '_locale_stats', '_browser_stats', '_search_stats'
                 ]
        for f in funcs:
            log.info('Downloading analytics for %s' % f.split('_')[1])
            getattr(self, f)(start_date, end_date, period_name, period_complete_day)

    def _get_results(result_data, f):
        data = {}
        for result in result_data:
            key = f(result)
            data[key] = data.get(key,0) + result[1]
        return data

    def _get_ga_data(self, params):
        '''Returns the GA data specified in params.
        Does all requests to the GA API and retries if needed.

        Returns a dict with the data, or dict(url=[]) if unsuccessful.
        '''
        try:
            data = self._get_ga_data_simple(params)
        except DownloadError:
            log.info('Will retry requests after a pause')
            time.sleep(300)
            try:
                data = self._get_ga_data_simple(params)
            except DownloadError:
                return dict(url=[])
            except Exception, e:
                log.exception(e)
                log.error('Uncaught exception in get_ga_data_simple (see '
                          'above)')
                return dict(url=[])
        except Exception, e:
            log.exception(e)
            log.error('Uncaught exception in get_ga_data_simple (see above)')
            return dict(url=[])
        return data

    def _get_ga_data_simple(self, params):
        '''Returns the GA data specified in params.
        Does all requests to the GA API.

        Returns a dict with the data, or raises DownloadError if unsuccessful.
        '''
        ga_token_filepath = os.path.expanduser(
            config.get('googleanalytics.token.filepath', ''))
        if not ga_token_filepath:
            log.error('In the CKAN config you need to specify the filepath '
                      'of the Google Analytics token file under key: '
                      'googleanalytics.token.filepath')
            return

        try:
            from ga_auth import init_service
            self.token, svc = init_service(ga_token_filepath, None)
        except Exception, auth_exception:
            log.error('OAuth refresh failed')
            log.exception(auth_exception)
            return dict(url=[])

        headers = {'authorization': 'Bearer ' + self.token}
        response = self._do_ga_request(params, headers)
        # allow any exceptions to bubble up

        data_dict = response.json()

        # If there are 0 results then the rows are missed off, so add it in
        if 'rows' not in data_dict:
            data_dict['rows'] = []
        return data_dict

    def _edit_url(self, data):
        '''Adds either data.gov or catalog.data.gov to the front of urls'''
        newDict = {}
        for k,v in data.iteritems():
            if k == "/":
                newDict["data.gov" + k] = v
                continue
            try:
                requestUrl = "https://www.data.gov" + k
                r = requests.get(requestUrl)
                if r.status_code == 200:
                    newDict["data.gov" + k] = v
                else:
                    newDict["catalog.data.gov" + k] = v	
            except Exception as e:
                newDict["catalog.data.gov" + k] = v
        return newDict

    @classmethod
    def _do_ga_request(cls, params, headers):
        '''Makes a request to GA. Assumes the token init request is already done.

        Returns the response (requests object).
        On error it logs it and raises DownloadError.
        '''
        # Because of issues of invalid responses when using the ga library, we
        # are going to make these requests ourselves.
        ga_url = 'https://www.googleapis.com/analytics/v3/data/ga'
        try:
            response = requests.get(ga_url, params=params, headers=headers)
        except requests.exceptions.RequestException, e:
            log.error("Exception getting GA data: %s" % e)
            raise DownloadError()
        if response.status_code != 200:
            log.error("Error getting GA data: %s %s" % (response.status_code,
                                                        response.content))
            raise DownloadError()
        return response

    def _totals_stats(self, start_date, end_date, period_name, period_complete_day):
        """ Fetches distinct totals, total pageviews etc """
        try:
            args = {}
            args["max-results"] = 100000
            args["start-date"] = start_date
            args["end-date"] = end_date
            args["ids"] = "ga:" + self.profile_id

            args["metrics"] = "ga:pageviews"
            args["sort"] = "-ga:pageviews"
            args["alt"] = "json"

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        ga_model.update_sitewide_stats(period_name, "Totals", {'Total page views': result_data[0][0]},
            period_complete_day)

        try:
            args = {}
            args["max-results"] = 100000
            args["start-date"] = start_date
            args["end-date"] = end_date
            args["ids"] = "ga:" + self.profile_id

            args["metrics"] = "ga:pageviewsPerSession,ga:avgTimeOnPage,ga:percentNewSessions,ga:sessions,ga:newUsers,ga:avgSessionDuration,ga:bounceRate,ga:percentSessionsWithSearch"
            args["alt"] = "json"

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        data = {
            'Pages per visit': result_data[0][0],
            'Average time on a page': result_data[0][1],
            'New users percentage': result_data[0][2],
            'Total visits': result_data[0][3],
            'New users': result_data[0][4],
            'Average time on site': result_data[0][5],
            'Bounce rate': result_data[0][6],
            'Percent with search': result_data[0][7]
        }
        ga_model.update_sitewide_stats(period_name, "Totals", data, period_complete_day)

        # Bounces from / or another configurable page.
        path = '/%s%s' % (config.get('googleanalytics.account'),
                          config.get('ga-report.bounce_url', '/'))

        try:
            args = {}
            args["max-results"] = 100000
            args["start-date"] = start_date
            args["end-date"] = end_date
            args["ids"] = "ga:" + self.profile_id
            args["filters"] = 'ga:pagePath==%s' % path
            args["dimensions"] = 'ga:pagePath'
            args["metrics"] = "ga:visitBounceRate"
            args["alt"] = "json"

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        if not result_data or len(result_data) != 1:
            log.error('Could not pinpoint the bounces for path: %s. Got results: %r',
                      path, result_data)
            return
        results = result_data[0]
        bounces = float(results[1])
        # visitBounceRate is already a %
        log.info('Google reports visitBounceRate as %s', bounces)
        ga_model.update_sitewide_stats(period_name, "Totals", {'Bounce rate (home page)': float(bounces)},
            period_complete_day)


    def _locale_stats(self, start_date, end_date, period_name, period_complete_day):
        """ Fetches stats about language and country """

        try:
            args = {}
            args["max-results"] = 100000
            args["start-date"] = start_date
            args["end-date"] = end_date
            args["ids"] = "ga:" + self.profile_id

            args["dimensions"] = "ga:language"
            args["metrics"] = "ga:pageviews"
            args["sort"] = "-ga:pageviews"
            args["alt"] = "json"

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
            count = count + 1
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Languages", data, period_complete_day)

        try:
            args = {}
            args["max-results"] = 100000
            args["start-date"] = start_date
            args["end-date"] = end_date
            args["ids"] = "ga:" + self.profile_id

            args["dimensions"] = "ga:country"
            args["metrics"] = "ga:pageviews"
            args["sort"] = "-ga:pageviews"
            args["alt"] = "json"

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
            count = count + 1
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Country", data, period_complete_day)

        try:
            args = {}
            args["max-results"] = 100000
            args["start-date"] = start_date
            args["end-date"] = end_date
            args["ids"] = "ga:" + self.profile_id

            args["dimensions"] = "ga:region"
            args["metrics"] = "ga:pageviews"
            args["sort"] = "-ga:pageviews"
            args["alt"] = "json"

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
            count = count + 1
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Region", data, period_complete_day)

        try:
            args = {}
            args["max-results"] = 100000
            args["start-date"] = start_date
            args["end-date"] = end_date
            args["ids"] = "ga:" + self.profile_id

            args["dimensions"] = "ga:metro"
            args["metrics"] = "ga:pageviews"
            args["sort"] = "-ga:pageviews"
            args["alt"] = "json"

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
            count = count + 1
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Metro", data, period_complete_day)

    def _download_stats(self, start_date, end_date, period_name, period_complete_day):
        """ Fetches stats about data downloads """

        data = {}
        identifier = ga_model.Identifier()

        try:
            args = {}
            args["max-results"] = 100000
            args["start-date"] = start_date
            args["end-date"] = end_date
            args["ids"] = "ga:" + self.profile_id

            args["filters"] = 'ga:eventAction==download'
            args["dimensions"] = "ga:pagePath"
            args["metrics"] = "ga:totalEvents"
            args["sort"] = "-ga:totalEvents"
            args["alt"] = "json"

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        if not result_data:
            # We may not have data for this time period, so we need to bail
            # early.
            log.info("There is no download data for this time period")
            return

        def process_result_data(result_data):
            resources_not_matched = []
            for result in result_data:
                page_path, total_events = result
                #e.g. page=u'/data.gov.uk/dataset/road-accidents-safety-data'
                page_path = strip_off_host_prefix(page_path)  # strips off domain
                # Get package id associated with the resource that has this URL.
                package_name = identifier.get_package(page_path)
                if package_name:
                    data[package_name] = data.get(package_name, 0) + int(total_events)
                else:
                    resources_not_matched.append(page_path)
                    continue
            if resources_not_matched:
                log.debug('Could not match %i of %i resource URLs to datasets. e.g. %r',
                          len(resources_not_matched), len(result_data), resources_not_matched[:3])

        log.info('Associating downloads of resource URLs with their respective datasets')
        process_result_data(results.get('rows'))

        try:
            args['filters'] = 'ga:eventAction==download-cache'

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])
        result_data = results.get('rows')
        if not result_data:
            # We may not have data for this time period, so we need to bail
            # early.
            log.info("There is no cached download data for this time period")
            return
        log.info('Associating cached downloads of resource URLs with their respective datasets')
        process_result_data(results.get('rows'))

        ga_model.update_sitewide_stats(period_name, "Downloads", data, period_complete_day)

    def _referral_stats(self, start_date, end_date, period_name, period_complete_day):
        """ Finds out which social sites people are referred from """

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:pageviews',
                         sort='-ga:pageviews',
                         dimensions="ga:socialNetwork",
                         max_results=10000)
            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        data = {}
        for result in result_data:
            if not result[0] == '(not set)':
                data[result[0]] = data.get(result[0], 0) + int(result[1])
        self._filter_out_long_tail(data, 3)
        ga_model.update_sitewide_stats(period_name, "Social sources", data, period_complete_day)

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:pageviews',
                         sort='-ga:pageviews',
                         dimensions="ga:referralPath",
                         max_results=10000)
            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        data = {}
        for result in result_data:
            if not result[0] == '(not set)':
                data[result[0]] = data.get(result[0], 0) + int(result[1])
        self._filter_out_long_tail(data, 3)
        ga_model.update_sitewide_stats(period_name, "Referral sources", data, period_complete_day)


    def _os_stats(self, start_date, end_date, period_name, period_complete_day):
        """ Operating system stats """
        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:pageviews',
                         sort='-ga:pageviews',
                         dimensions="ga:operatingSystem,ga:operatingSystemVersion",
                         max_results=10000)
            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        data = {}
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[2])
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Operating Systems", data, period_complete_day)

        data = {}
        for result in result_data:
            if int(result[2]) >= MIN_VIEWS:
                key = "%s %s" % (result[0],result[1])
                data[key] = result[2]
        ga_model.update_sitewide_stats(period_name, "Operating Systems versions", data, period_complete_day)


    def _browser_stats(self, start_date, end_date, period_name, period_complete_day):
        """ Information about browsers and browser versions """

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:pageviews',
                         sort='-ga:pageviews',
                         dimensions="ga:browser",
                         max_results=10000)

            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        # e.g. [u'Firefox', u'19.0', u'20']
       
        data = {}
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Browsers", data, period_complete_day)

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:pageviews',
                         sort='-ga:pageviews',
                         dimensions="ga:browserSize",
                         max_results=10000)

            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        # e.g. [u'Firefox', u'19.0', u'20']

        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
            count = count + 1
            if count == 50:
                break
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Browser sizes", data, period_complete_day)

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:pageviews',
                         sort='-ga:pageviews',
                         dimensions="ga:deviceCategory",
                         max_results=10000)

            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        # e.g. [u'Firefox', u'19.0', u'20']

        data = {}
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Device category", data, period_complete_day)

    def _page_stats(self, start_date, end_date, period_name, period_complete_day):
        """ Information about browsers and browser versions """

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:pageviews,ga:avgTimeOnPage',
                         sort='-ga:pageviews',
                         dimensions="ga:pagePath",
                         max_results=10000)

            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        # e.g. [u'Firefox', u'19.0', u'20']

        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
            count = count + 1
            if count == 100:
                break

        self._filter_out_long_tail(data, MIN_VIEWS)
        data = self._edit_url(data)	
        ga_model.update_sitewide_stats(period_name, "Page views", data, period_complete_day)

        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + float(result[2])
            count = count + 1
            if count == 100:
                break
        self._filter_out_long_tail(data, MIN_VIEWS)
        data = self._edit_url(data)
        ga_model.update_sitewide_stats(period_name, "Page avgTime", data, period_complete_day)

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:entrances',
                         sort='-ga:entrances',
                         dimensions="ga:landingPagePath",
                         max_results=10000)

            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        # e.g. [u'Firefox', u'19.0', u'20']

        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
            count = count + 1
            if count == 100:
                break
        self._filter_out_long_tail(data, MIN_VIEWS)
        data = self._edit_url(data)
        ga_model.update_sitewide_stats(period_name, "Landing page", data, period_complete_day)

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:exits',
                         sort='-ga:exits',
                         dimensions="ga:exitPagePath",
                         max_results=10000)

            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        # e.g. [u'Firefox', u'19.0', u'20']

        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
            count = count + 1
            if count == 100:
                break
        self._filter_out_long_tail(data, MIN_VIEWS)
        data = self._edit_url(data)
        ga_model.update_sitewide_stats(period_name, "Exit page", data, period_complete_day)

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:entrances',
                         sort='-ga:entrances',
                         dimensions="ga:pagePathLevel1",
                         max_results=10000)

            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')

        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
            count = count + 1
            if count == 100:
                break
        self._filter_out_long_tail(data, MIN_VIEWS)
        data = self._edit_url(data)
        ga_model.update_sitewide_stats(period_name, "Second page", data, period_complete_day)

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:entrances',
                         sort='-ga:entrances',
                         dimensions="ga:pagePathLevel2",
                         max_results=10000)

            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')

        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
            count = count + 1
            if count == 100:
                break
        self._filter_out_long_tail(data, MIN_VIEWS)
        data = self._edit_url(data)
        ga_model.update_sitewide_stats(period_name, "Third page", data, period_complete_day)

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:timeOnPage',
                         sort='-ga:timeOnPage',
                         dimensions="ga:pagePath",
                         max_results=10000)

            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')

        data = {}
        count = 0
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + float(result[1])
            count = count + 1
            if count == 100:
                break
        self._filter_out_long_tail(data, MIN_VIEWS)
        data = self._edit_url(data)
        ga_model.update_sitewide_stats(period_name, "Time on page", data, period_complete_day)

    @classmethod
    def _filter_browser_version(cls, browser, version_str):
        '''
        Simplifies a browser version string if it is detailed.
        i.e. groups together Firefox 3.5.1 and 3.5.2 to be just 3.
        This is helpful when viewing stats and good to protect privacy.
        '''
        ver = version_str
        parts = ver.split('.')
        if len(parts) > 1:
            if parts[1][0] == '0':
                ver = parts[0]
            else:
                ver = "%s" % (parts[0])
        # Special case complex version nums
        if browser in ['Safari', 'Android Browser']:
            ver = parts[0]
            if len(ver) > 2:
                num_hidden_digits = len(ver) - 2
                ver = ver[0] + ver[1] + 'X' * num_hidden_digits
        return ver

    def _mobile_stats(self, start_date, end_date, period_name, period_complete_day):
        """ Info about mobile devices """

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:pageviews',
                         sort='-ga:pageviews',
                         dimensions="ga:mobileDeviceBranding, ga:mobileDeviceInfo",
                         max_results=10000)
            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])


        result_data = results.get('rows')
        data = {}
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[2])
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Mobile brands", data, period_complete_day)

        data = {}
        for result in result_data:
            data[result[1]] = data.get(result[1], 0) + int(result[2])
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Mobile devices", data, period_complete_day)

    def _search_stats(self, start_date, end_date, period_name, period_complete_day):
        """ Info about mobile devices """

        try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:searchResultViews',
                         sort='-ga:searchResultViews',
                         dimensions="ga:searchKeyword",
                         max_results=10000)
            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        data = {}
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
        self._filter_out_long_tail(data, MIN_VIEWS)
        ga_model.update_sitewide_stats(period_name, "Search keywords", data, period_complete_day)

	try:
            args = dict( ids='ga:' + self.profile_id,
                         metrics='ga:searchResultViews',
                         sort='-ga:searchResultViews',
                         dimensions="ga:searchAfterDestinationPage",
                         max_results=10000)
            args['start-date'] = start_date
            args['end-date'] = end_date

            results = self._get_ga_data(args)
        except Exception, e:
            log.exception(e)
            results = dict(url=[])

        result_data = results.get('rows')
        data = {}
        for result in result_data:
            data[result[0]] = data.get(result[0], 0) + int(result[1])
        self._filter_out_long_tail(data, MIN_VIEWS)
	data = self._edit_url(data)
        ga_model.update_sitewide_stats(period_name, "Search destination page", data, period_complete_day)
    @classmethod
    def _filter_out_long_tail(cls, data, threshold=10):
        '''
        Given data which is a frequency distribution, filter out
        results which are below a threshold count. This is good to protect
        privacy.
        '''
        for key, value in data.items():
            if value < threshold:
                del data[key]

global host_re
host_re = None


def strip_off_host_prefix(url):
    '''Strip off the hostname that gets prefixed to the GA Path on data.gov.uk
    UA-1 but not on others.

    >>> strip_off_host_prefix('/data.gov.uk/dataset/weekly_fuel_prices')
    '/dataset/weekly_fuel_prices'
    >>> strip_off_host_prefix('/dataset/weekly_fuel_prices')
    '/dataset/weekly_fuel_prices'
    '''
    global host_re
    if not host_re:
        host_re = re.compile('^\/[^\/]+\.')
    # look for a dot in the first part of the path
    if host_re.search(url):
        # there is a dot, so must be a host name - strip it off
        return '/' + '/'.join(url.split('/')[2:])
    return url


class DownloadError(Exception):
    pass
