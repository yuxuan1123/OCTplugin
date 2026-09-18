"""translation/apps —— 五种实时功能应用包

    screen_ocr / one_shot / realtime / subtitle / speech_translate
"""

from apps.screen_ocr_app import ScreenOcrApp
from apps.one_shot_screen_translate_app import OneShotScreenTranslateApp
from apps.realtime_screen_translate_app import RealtimeScreenTranslateApp
from apps.screen_subtitle_app import ScreenSubtitleApp
from apps.speech_translate_app import SpeechTranslateApp

__all__ = [
    "ScreenOcrApp",
    "OneShotScreenTranslateApp",
    "RealtimeScreenTranslateApp",
    "ScreenSubtitleApp",
    "SpeechTranslateApp",
]