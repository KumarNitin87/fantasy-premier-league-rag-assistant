from config.settings import Settings


def test_application_settings_do_not_require_an_ai_provider():
    settings = Settings(_env_file=None)

    assert settings.APP_NAME == "PL Fantasy Predictor"
    assert not hasattr(settings, "GEMINI_API_KEY")
    assert settings.NEWS_REFRESH_INTERVAL_HOURS == 6
