from collections import namedtuple
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from threading import Timer

import pandas as pd
import pytz

from pylendingclub import config
from ..errors import AvailableLoansError


class LendingClubAutoInvestor(object):
    def _unpack_confirmations(self, confirmations):
        unpacked = []
        instruction_id = confirmations['orderInstructId']

        for confirmed_order in confirmations['orderConfirmations']:
            confirmed_order.update({'orderInstructId' : instruction_id})
            unpacked.append(confirmed_order)
        return unpacked


    def __print(self, *args, **kwargs):
        if self.__verbose:
            print(*args, **kwargs)

        if self.__log_path is not None:
            with open(self.__log_path, 'a+') as f:
                print((*args + ('\n', )), file=f, **kwargs)

    def _validate_value(self, value):
        if value > 0:
            return value
        else:
            return 25

    @property
    def _available_cash(self):
        return self.session.available_cash(as_string=False)

    @property
    def _investment_amount(self):
        return (self._available_cash // self._denomination) * self._denomination

    @property
    def _num_notes(self):
        return self._investment_amount // self._investment_per_note

    @property
    def _denomination(self):
        return self.__denomination

    @_denomination.getter
    def _denomination(self):
        return self.__denomination

    @_denomination.setter
    def _denomination(self, value):
        self.__denomination = self._validate_value(value)

    @property
    def _investment_per_note(self):
        return self.__investment_per_note

    @_investment_per_note.getter
    def _investment_per_note(self):
        return self.__investment_per_note

    @_investment_per_note.setter
    def _investment_per_note(self, value):
        self.__investment_per_note = self._validate_value(value)

    @property
    def _expired(self):
        if self.__expiration:
            self.__print('Self Expiration: ', self.__expiration)
            self.__print('Current Time: ', datetime.utcnow())

            return self.__expiration < datetime.utcnow()

        raise ValueError('Expiration time not set.')

    def _next_scheduled_time(self, timezone='LOCAL'):
        supported_timezone_keys = ['LOCAL', 'PLATFORM', 'UTC']
        supported_timezone_items = [self._LOCAL_TIMEZONE,
                                    self._PLATFORM_TIMEZONE, self._UTC_TIMEZONE]

        if timezone.upper() in supported_timezone_keys:
            timezones = dict(zip(supported_timezone_keys, supported_timezone_items))
            return str(self.__next_run_time.astimezone(timezones[timezone]))
        raise NotImplementedError

    def _next_run_time(self):
        now = datetime.utcnow()
        year, month, day, hour, minute = now.year, now.month, now.day, now.hour, now.minute
        current_time = datetime(year, month, day, hour, minute, tzinfo=pytz.utc)
        self.__print('Current Time: ', current_time)

        for time in self._times:
            scheduled_time = datetime(year, month, day + time.dayoffset,
                                      time.hour, time.minute, tzinfo=pytz.utc)
            if current_time < scheduled_time:
                return scheduled_time

        self.__print('Unable to schedule next run time.', flush=True)
        self.__print('Current Time: ', current_time)
        self.__print('Available Times: ', self._times)

    def __set_expiration(self):
        self.__expiration = datetime.utcnow() + timedelta(minutes=self._minutes_to_expire)

    def __schedule_next_run(self):
        next_run_time = self._next_run_time()
        now = pytz.utc.localize(datetime.utcnow())
        time_delta = next_run_time - now
        self.__scheduled_thread = Timer(time_delta.seconds, self.__start)
        self.__scheduled_thread.start()
        self.__next_run_time = next_run_time
        self.__print('Next Run Time: \n', 'PLATFORM', self._next_scheduled_time('PLATFORM'), '\n',
                     'LOCAL:', self._next_scheduled_time('LOCAL'), '\n',
                     'UTC:', self._next_scheduled_time('UTC'), '\n',
                     flush=True)

    def __invest(self):
        self.__print('Investing...', flush=True)
        response = None
        try:
            response = self.session.invest(total_amount=self._investment_amount,
                                           amount_per_note=self._investment_per_note,
                                           portfolio_id=self._portfolio,
                                           listing_filter=self._listings_filter)
        except AvailableLoansError as e:
            self.__print('No loans available to invest in...')
        except:
            self.__print('Error while investing...', flush=True)
            raise
        else:
            self.__print('Investment Response: ', response, flush=True)

            if response is not None:
                self.__print('Response Successful: ', response.status_code == 200, flush=True)
                if response.status_code == 200:
                    self.__confirmations += self._unpack_confirmations(response.json())

    def __start(self):
        if self.__verbose:
            self.__print('Starting...', flush=True)

        self.__set_expiration()
        self.__next_run_time = None

        self.__is_running = True

        self.__print('Have Notes to Invest: ', self.have_notes_to_invest, flush=True)
        self.__print('Is Expired: ', self._expired, flush=True)

        if self.have_notes_to_invest and not self._expired:
            self.__invest()

        self.__print('About to stop...', flush=True)
        self.__stop()

    def __stop(self):
        self.__print('Stopping...', flush=True)
        self.__is_running = False
        self.__expiration = None
        if self.have_notes_to_invest:
            self.__schedule_next_run()

    def __cancel(self):
        if self.__scheduled_thread:
            self.__scheduled_thread.cancel()
            self.__scheduled_thread = None

        self.__next_run_time = None
        self.__expiration = datetime.utcnow()

    def start(self):
        self.__print('Attempting to invest once...', flush=True)
        self.invest_once()

        if self._num_notes > 0:
            self.__print('Funds still remaining. Scheduling next run...', flush=True)
            self.__schedule_next_run()
            self.__print('Next run scheduled...', flush=True)

    def stop(self):
        self.__cancel()

    def invest_once(self):
        if self._num_notes > 0:
            return self.__invest()

    @property
    def is_scheduled(self):
        return self.__scheduled_thread is not None

    @property
    def is_running(self):
        return self.__is_running

    @property
    def have_notes_to_invest(self):
        return self._num_notes > 0

    @property
    def status(self):
        if self.is_scheduled:
            return 'Scheduled to run at: ' + self._next_scheduled_time('LOCAL')
        elif self.is_running:
            return 'Running'
        elif self._available_cash < self._denomination:
            return 'Not Enough Funds : Current Balance is {}'.format(self._available_cash)
        else:
            return 'Undefined'

    @property
    def confirmations(self, as_dataframe=True):
        confirmations_summary = []

        if len(self.__confirmations) > 0:
            for confirmation in self.__confirmations:
                confirmations_summary.append(confirmation.data)

            if as_dataframe:
                return pd.DataFrame(confirmations_summary)

            return confirmations_summary

    def __init__(self,
                 session,
                 denomination=25,
                 investment_per_note=25,
                 filter_name=None,
                 portfolio_name=None,
                 minutes_until_expiration=1,
                 listings_filter=None,
                 verbose=False,
                 log_path=None):

        self.session = session
        self.__verbose = verbose
        self.__log_path = log_path
        self._log_path = log_path

        ScheduledTime = namedtuple('ScheduledTime', 'dayoffset hour minute')

        # Sort order is important
        self._times = [ScheduledTime(0, 12, 59),
                       ScheduledTime(0, 16, 59),
                       ScheduledTime(0, 20, 59),
                       ScheduledTime(1, 0, 59)]

        self._filter = self.session.filter_id_by_name(filter_name)
        self._portfolio = self.session.portfolio_id_by_name(portfolio_name)

        self._denomination = denomination
        self._investment_per_note = investment_per_note

        self._minutes_to_expire = minutes_until_expiration

        self._listings_filter = listings_filter

        self.__next_run_time = None
        self.__expiration_time = None
        self.__scheduled_thread = None
        self.__is_running = False

        self.__confirmations = []

        self._UTC_TIMEZONE = pytz.utc
        self._PLATFORM_TIMEZONE = pytz.timezone("America/Los_Angeles")
        self._LOCAL_TIMEZONE = datetime.now(timezone.utc).astimezone().tzinfo

    def __del__(self):
        self.__cancel()


class InvestorScheduler(object):
    """
    Handles the automatic creation of investor processes when there are available funds.
    """

    def __validate_delay_value(self, value):
        if value < 0:
            value *= -1
        elif value == 0:
            value = 1
        return value

    @property
    def __investor_started(self):
        return self.__investor.is_running or self.__investor.is_scheduled

    def __start_investor(self):
        if not self.__investor_started:
            self.__investor.start()

    def __stop_investor(self):
        if self.__investor_started:
            self.__investor.stop()

    def __start(self):
        self.__start_investor()

    def __stop(self):
        self.__stop_investor()

    def start(self):
        self.__start()
        self.__scheduled_thread = Timer(self.__minutes_between_checks*60, self.__start)
        self.__scheduled_thread.start()

    def stop(self):
        self.__scheduled_thread = None
        self.__stop_investor()

    @property
    def investor_is_scheduled(self):
        if self.__investor:
            return self.__investor.is_scheduled
        return False

    @property
    def investor_is_running(self):
        if self.__investor:
            return self.__investor.__is_running
        return False

    @property
    def investor_status(self):
        if self.__investor:
            return self.__investor.status
        return "Investor not created yet."

    @property
    def status(self):
        if self.__investor:
            return "Investor Created. Investor Status:\n" + self.__investor.status
        else:
            return "Undefined. No investor Created."

    def __init__(self,
                 session=None,
                 investor=None,
                 minutes_until_expiration=1,
                 minutes_between_checks=24*60,
                 **kwargs):

        #if investment_per_note < config.LC_MIN_NOTE_INVESTMENT:
        #    raise ValueError('Minimum Note Investment amount is {}.'.format(
        #        config.LC_MIN_NOTE_INVESTMENT))

        #if investment_per_note % config.LC_INVESTMENT_DENOMINATION != 0:
        #    raise ValueError('Investment Per Note {} must be a multiple of {}.'.format(
        #        investment_per_note, config.LC_INVESTMENT_DENOMINATION))

        #self.__denomination = 25
        #self.__investment_per_note = investment_per_note
        self.__minutes_until_expiration = self.__validate_delay_value(minutes_until_expiration)
        self.__minutes_between_checks = self.__validate_delay_value(minutes_between_checks)
        #self.__filter_name = filter_name
        #self.__verbose = verbose

        if investor is not None:
            self.__investor = investor
        else:
            if session is None:
                from pylendingclub.wrapper.session import ExtendedLendingClubSession
                session = ExtendedLendingClubSession()

            self.__investor = LendingClubAutoInvestor(self.__session,
                                                      **kwargs)

        self.__scheduled_thread = None

    def __del__(self):
        self.__scheduled_thread = None
        self.__stop_investor()
