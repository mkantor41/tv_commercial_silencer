import json
import os
import logging_util
import media_util

from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from datetime import timedelta

from dejavu import Dejavu
from dejavu.recognize import FileRecognizer, MicrophoneRecognizer

import tv_service

import warnings
warnings.filterwarnings("ignore")

# Reference example.py

# instantiate at module level, not class level
# https://stackoverflow.com/questions/22807972/python-best-practice-in-terms-of-logging
logger = logging_util.get_logger(__name__)


class AudioRecognizer:

    def __init__(self):
        # flag may be used to enable or disable calling tv_service.
        # May be used to prevent multiple calls for one commercial.
        self.is_call_tv_service_enabled = True

        self.background_scheduler = BackgroundScheduler()
        # apscheduler job store- use default MemoryJobStore, in memory, not persisted
        # apscheduler executor- use default
        # can add jobs before or after starting scheduler
        self.background_scheduler.start()

        AudioRecognizer.config_environment_variable_database_url_from_file('data/config.json')

        # FIXME: default is unexpected argument??
        dburl = os.getenv('DATABASE_URL', default='sqlite://')
        logger.debug('dburl: {}'.format(dburl))

        # instantiate a Dejavu object, configured to use database at dburl
        self.djv = Dejavu(dburl=dburl)

    @staticmethod
    def config_environment_variable_database_url_from_file(filename):
        """
        Reads configuration file, may set environment variable DATABASE_URL.
        https://stackoverflow.com/questions/5627425/what-is-a-good-way-to-handle-exceptions-when-trying-to-read-a-file-in-python
        Alternatively could set a python variable.
        :param filename: a json file that may contain a dictionary with key "DATABASE_URL"
        """
        try:
            with open(filename) as f:
                config_dict = json.load(f)
                db_url_from_file = config_dict.get('DATABASE_URL')
                if db_url_from_file is not None:
                    os.environ['DATABASE_URL'] = db_url_from_file

        except IOError:
            # e.g. file doesn't exist
            logger.debug("Could not read file: " + filename)
            print("Could not read file: " + filename)

    @staticmethod
    def recognize_audio_from_a_file(djv, filename_containing_audio_to_match):
        """
        Shows example usage of djv.recognize, prints match_dict
        :param djv: a dejavu instance, preconfigured by having run fingerprint_directory
        :param filename_containing_audio_to_match:
        """
        match_dict = djv.recognize(FileRecognizer, filename_containing_audio_to_match)
        match_dict_json = json.dumps(match_dict)
        logger.debug('filename_containing_audio_to_match: {0}, match_dict_json: {1}\n'
                     .format(filename_containing_audio_to_match, match_dict_json))

        # example output
        # filename_containing_audio_to_match: mp3/chantix.mp3,
        # match_dict_json: {"song_id": 12, "song_name": "chantix", "confidence": 43335,
        # "offset": 0, "offset_seconds": 0.0,
        # "file_sha1": "7050797273712b325559706c4d6878594238583866486d4b4371493d0a",
        # "match_time": 11.098071813583374}

    @staticmethod
    def time_remaining_seconds(duration_seconds, offset_seconds, sample_window_seconds):
        """
        :param duration_seconds: commercial audio file duration in seconds
        :param offset_seconds: e.g. from dejavu match_dict
            From monitoring logs, offset_seconds appears to range from approximately
            -seconds <= offset_seconds <= (duration_seconds - seconds)
            alternatively add seconds to each term:
            0 <= (offset_seconds + seconds) <= duration_seconds
        :param sample_window_seconds: dejavu sampling window
        :return: estimated time remaining in commercial in seconds

        Example:
        duration_seconds == 60.0
        offset_seconds == 12 seconds
        dejavu matched live audio to commercial elapsed time == 12 seconds
        sample_window_seconds == 5
        return 60.0 - (12 + 5) == 43 seconds
        """
        duration_remaining_seconds = duration_seconds - (offset_seconds + sample_window_seconds)
        return duration_remaining_seconds

    def enable_call_tv_service(self, should_enable):
        """
        background_scheduler may call this function to set self.is_call_tv_service_enabled
        :param should_enable: True to enable calling tv_service
        """
        self.is_call_tv_service_enabled = should_enable

    def recognize_audio_from_microphone(self, djv, seconds=5):
        """
        method samples 'seconds' number of seconds
        :param djv: a dejavu instance, preconfigured by having run fingerprint_directory
        :param seconds: number of seconds to recognize audio
        :return: match_dict if confidence is >= confidence_minimum, else None
        """
        match_dict = djv.recognize(MicrophoneRecognizer, seconds=seconds)
        logger.debug('match_dict: {}'.format(match_dict))
        # example output
        # 2019-04-22 17:47:34 DEBUG    recognize_audio_from_microphone line:79 match_dict:
        # {'song_id': 4, 'song_name': 'google-help-cooper', 'confidence': 146,
        # 'offset': 17, 'offset_seconds': 0.78948, 'file_sha1': '5b2709b5d22011c18f9a7b6ab7f04f0e89da4d41'}

        if match_dict is None:
            # "Nothing recognized -- did you play the song out loud so your mic could hear it? :)"
            return None

        else:
            # use confidence_minimum to help avoid false positives,
            # e.g. avoid algorithm accidentally matching to background noise with confidence ~ 10
            # by manual observation of logs, 100 seems overly conservative
            confidence_minimum = 40
            confidence = match_dict.get('confidence')

            if confidence is not None and confidence >= confidence_minimum:
                commercial_name = match_dict.get('song_name')
                duration_seconds = media_duration_dict.get(commercial_name)
                offset_seconds = match_dict.get('offset_seconds')
                duration_remaining_seconds = AudioRecognizer.time_remaining_seconds(duration_seconds,
                                                                                    offset_seconds, seconds)

                duration_remaining_seconds_min = 8
                if duration_remaining_seconds >= duration_remaining_seconds_min:
                    # duration_remaining_seconds is long enough for tv service
                    # to emulate multiple remote control button presses.

                    logger.debug('is_call_tv_service_enabled: {}'.format(self.is_call_tv_service_enabled))

                    if self.is_call_tv_service_enabled:
                        # Don't call mute, too easy for app to get toggle confused
                        tv_service.volume_decrease_increase(duration_seconds=duration_remaining_seconds)

                        # disable calling tv service again until duration_remaining_seconds has elapsed
                        # this prevents multiple calls for one commercial
                        self.is_call_tv_service_enabled = False
                        # at run_date, scheduler will re-enable calling tv service
                        run_date = datetime.now() + timedelta(seconds=duration_remaining_seconds)
                        # add_job, implicitly create the trigger
                        # args is for function enable_call_tv_service
                        self.background_scheduler.add_job(self.enable_call_tv_service, 'date',
                                                          run_date=run_date, args=[True])

                return match_dict

        return None

    def recognize_audio_from_microphone_with_count(self, djv, seconds=5, count_max=4):
        """
        :param djv: a dejavu instance, preconfigured by having run fingerprint_directory
        :param seconds: number of seconds to recognize audio
        :param count_max: number of times to iterate
        :return:
        """
        for count in range(0, count_max):
            iteration = count + 1
            logger.debug(msg='{}/{}'.format(iteration, count_max))

            # waits for recognize_audio_from_microphone to return
            # recognize_audio_from_microphone returns shortly after 'seconds' number of seconds
            match_dict = self.recognize_audio_from_microphone(djv, seconds)


if __name__ == '__main__':

    audio_recognizer = AudioRecognizer()

    media_dict_filename = './data/media_durations_second.json'
    # update media dictionary
    media_util.write_media_file_durations('./data/commercial_mp3', media_dict_filename)
    media_duration_dict = media_util.media_durations_second_dict(media_dict_filename)
    logger.debug(media_duration_dict)

    # Prepare djv to be able to recognize audio.
    # Fingerprint all the mp3's in the directory we give it
    # this may take several seconds per file
    audio_recognizer.djv.fingerprint_directory("data/commercial_mp3", [".mp3"])

    # example, may be useful for debugging
    # AudioRecognizer.recognize_audio_from_a_file(audio_recognizer.djv,
    #                                             filename_containing_audio_to_match='data/commercial_mp3/chantix.mp3')

    audio_recognizer.recognize_audio_from_microphone_with_count(audio_recognizer.djv, seconds=5, count_max=40)
